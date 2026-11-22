"""Source-only Python reference for Bangumi lookup parity tests."""
import json
from hashlib import sha256
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote
from uuid import uuid4

from doujin import EVENT_TAG, Http, NoRedirect, event, iso_release_date, catalog_number_only
from native.export_library import archive_catalog_number, normalized


ROOT = "https://api.bgm.tv"
MUSIC = 3
LIMIT = 6
MISS_TTL = 7 * 86400
MATCH_TTL = 30 * 86400
LOOKUP_LEASE = 120


def infobox(subject):
    fields = {}
    for field in subject.get("infobox") or []:
        value = field.get("value")
        values = [value] if isinstance(value, str) else [entry.get("v") for entry in value or []]
        fields.setdefault(normalized(field["key"]), []).extend(part.strip() for part in values
                                                               if isinstance(part, str) and part.strip())
    return fields


def ordered_values(fields, keys):
    return [value for key in keys for value in fields.get(key, [])]


def event_ids(values):
    return {item["id"] for value in values for match in re.finditer(rf"\b{EVENT_TAG}\b", value, re.I)
            if (item := event(match[0]))}


def artists(values):
    return {normalized(credit) for value in values for credit in re.split(r"[()（）/、,，;；]+", value)
            if credit.strip()}


def match_release(release, subject, observed_catalogs):
    if subject.get("type") != MUSIC:
        return None
    if normalized(release["album"]) not in {normalized(subject.get(key) or "") for key in ("name", "name_cn")}:
        return None
    box = infobox(subject)
    album_artists = ordered_values(box, ("艺术家", "アーティスト", "artist", "album artist", "制作人"))
    local_artists = artists([release.get("album_artist") or "", release.get("artist") or ""])
    if not (local_artists & artists(album_artists)):
        return None

    label_values = ordered_values(box, ("厂牌", "レーベル", "label", "publisher", "出版方"))
    catalogs = {number for text in ordered_values(box, ("品番", "商品番号", "目录编号", "catalog number", "catalog"))
                if (number := catalog_number_only(text))}
    date_values = ordered_values(box, ("发售日期", "発売日", "release date"))
    subject_events = event_ids(date_values + ordered_values(box, ("イベント", "event", "release event")))
    local_events = {entry["id"] for entry in release.get("events", []) if entry.get("id")}
    local_events.update(event_ids(release.get("tags") or []))
    date_candidates = [iso_release_date(subject.get("date"))]
    date_candidates.extend(iso_release_date(text.split(" ", 1)[0]) for text in date_values)
    released = next((value for value in date_candidates if value), None)
    if local_events and subject_events and not (local_events & subject_events):
        return None
    if (local_date := iso_release_date(release.get("release_date"))) and released and local_date != released:
        return None
    if catalogs and observed_catalogs and not (catalogs & observed_catalogs):
        return None
    if not (local_events & subject_events or catalogs & observed_catalogs or (local_date and local_date == released)):
        return None
    if len(label_values) > 1 or len(catalogs) > 1:
        return None

    return {key: value for key, value in {
        "publisher": next(iter(label_values), None),
        "catalog_id": next(iter(catalogs), None),
        "release_date": released,
        "events": [event(identifier) for identifier in sorted(subject_events | local_events)],
    }.items() if value}


def get_json(http, path, *, payload=None):
    headers = {"User-Agent": "DoujinStyle-foobar2000/0.0.19 (release metadata lookup)",
               "Accept": "application/json"}
    data = json.dumps(payload).encode() if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    _, body, _ = http.get(ROOT + path, headers=headers, data=data, cap=524288, timeout=6)
    return json.loads(body)


def find_release(http, release, catalogs):
    title = release["album"]
    try:
        search = get_json(http, "/v0/search/subjects?limit=8",
                          payload={"keyword": title, "sort": "match", "filter": {"type": [MUSIC]}})
        hits = search.get("data") or []
    except HTTPError as exc:
        if exc.code not in (401, 403, 404, 405):
            raise
        search = get_json(http, "/search/subject/" + quote(title, safe="") + "?type=3&max_results=8")
        hits = search.get("list") or []
    ids = dict.fromkeys(hit["id"] for hit in hits[:8] if type(hit.get("id")) is int and
                       any(normalized(hit.get(key) or "") == normalized(title) for key in ("name", "name_cn")))
    matches = []
    for identifier in ids:
        subject = get_json(http, f"/v0/subjects/{identifier}")
        if match := match_release(release, subject, catalogs):
            matches.append((identifier, match))
    return matches[0] if len(matches) == 1 else None


def enrich_releases(cache, release_ids, emit, *, http=None, force=False):
    http = http or Http(NoRedirect())
    now = int(time.time())
    checked = 0
    for release_id in release_ids:
        release = cache.listing(release_id)
        if not release:
            continue
        prior = (release.get("external_lookup") or {}).get("bangumi") or {}
        ttl = MATCH_TTL if prior.get("url") else MISS_TTL
        explicit = catalog_number_only(release.get("catalog_id"))
        tagged = set()
        hints = set()
        for copy in cache.copies_for(release_id):
            archive = copy["archive"] or {}
            if number := archive_catalog_number(archive):
                tagged.add(number)
            hints.update(code for value in archive.get("catalog_id_candidates", []) if (code := catalog_number_only(value)))
        catalogs = {explicit} if explicit else tagged or hints
        events = sorted(event_ids(release.get("tags") or []) | {item["id"] for item in release.get("events") or []})
        stamp = sha256(json.dumps([release["album"], release.get("artist"), release.get("album_artist"),
                                  release.get("release_date"), sorted(catalogs), events]).encode()).hexdigest()
        if not (catalogs or events or iso_release_date(release.get("release_date"))):
            continue
        if not force and now - prior.get("checked", 0) < ttl and (
            stamp == prior.get("evidence") or (prior.get("url") and not prior.get("evidence"))
        ):
            continue
        token = uuid4().hex
        if not cache.claim_lookup("bangumi", release_id, token, now + LOOKUP_LEASE, now):
            continue
        checked += 1
        try:
            current = cache.listing(release_id).get("external_lookup") or {}
            if not force and current.get("bangumi") and current["bangumi"] != prior:
                continue
            try:
                matched = find_release(http, release, catalogs)
            except (OSError, TypeError, ValueError, KeyError, AttributeError) as exc:
                status = f"HTTP {exc.code}" if isinstance(exc, HTTPError) else type(exc).__name__
                emit(f"  {release_id}   Bangumi lookup unavailable ({status})")
                if checked >= LIMIT or isinstance(exc, HTTPError) and exc.code == 429:
                    return
                continue
            record = {"checked": now, "evidence": stamp}
            if matched:
                identifier, match = matched
                record.update(url=f"https://bgm.tv/subject/{identifier}", fields=match)
                emit(f"  {release_id}   Matched Bangumi album {identifier}")
            elif prior.get("url") and prior.get("evidence") in (None, stamp):
                record = {**prior, **record}
            cache.put_observation({"id": release_id, "external_lookup": {**current, "bangumi": record}})
        finally:
            cache.release_lookup("bangumi", release_id, token)
        if checked >= LIMIT:
            return
