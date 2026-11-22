#!/usr/bin/env python3
"""Search DoujinStyle and inspect release evidence and remote ZIPs."""
import argparse
import gzip
import io
import json
import os
import re
import sqlite3
import struct
import sys
import time
import unicodedata
import zipfile
from collections import Counter
from contextlib import nullcontext
from datetime import datetime
from http.cookiejar import CookieJar
from pathlib import Path, PurePosixPath
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from lxml import html
from native.reference_publisher import NativeCatalogWorkerUnavailable
from catalog_cache import CatalogCache, MEDIAFIRE_SHARE_HOSTS, can_inspect, destination_key
from native.worker_scope import standalone_only, worker_fallback, runtime_deprecated


DS = "https://doujinstyle.com/"
MB = "https://musicbrainz.org/"
UA = "DoujinCatalogPrototype/0.1 (interactive catalog lookup)"
HTML_LIMIT = 1_048_576
PREFIX_BYTES = 4096  # Shared with native audio_prefix; no Python prefix decoder in the installed worker.
CATNO = r"[A-Z][A-Z0-9]{1,17}[-‐‑–][A-Z0-9]{0,5}[0-9][A-Z0-9]{0,5}"
AUDIO = {".flac", ".mp3", ".m4a", ".wav", ".opus", ".ogg", ".tta", ".aiff", ".ape", ".wv"}
MEMBER_TYPES = {
    "audio_candidate": AUDIO | {
        ".aac", ".ac3", ".aif", ".alac", ".dff", ".dsf", ".mka", ".mp2", ".mp4", ".mpc",
        ".oga", ".tak", ".wma", ".mod", ".nsf", ".spc", ".vgm", ".vgz", ".xma", ".adx",
    },
    "artwork": {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"},
    "metadata": {".cue", ".log", ".md", ".nfo", ".sfv", ".txt", ".xml", ".json"},
    "playlist": {".m3u", ".m3u8", ".pls"},
    "archive": {".7z", ".gz", ".rar", ".tar", ".zip"},
    "document": {".pdf"},
}
EVENTS = {
    "M3-44": ("2019-10-27", "2019-10-27", "https://www.m3net.jp/diarypro/index.cgi?no=141"),
    "M3-54": ("2024-10-27", "2024-10-27", "https://www.m3net.jp/diarypro/index.cgi?date=20240522"),
    "C108": ("2026-08-15", "2026-08-16", "https://comiket.co.jp/info-a/TAFO/C108TAFO/index.html"),
    "C109": ("2026-12-29", "2026-12-31", "https://comiket.co.jp/info-a/TAFO/C109TAFO/index.html"),
    "M3-56": ("2025-10-26", "2025-10-26", "https://www.m3net.jp/diarypro/index.cgi?no=268"),
    "M3-57": ("2026-04-26", "2026-04-26", "https://www.m3net.jp/diarypro/index.cgi?no=275"),
    "M3-58": ("2026-10-25", "2026-10-25", "https://www.m3net.jp/diarypro/index.cgi?no=280"),
    "RTS19": ("2022-05-08", "2022-05-08", "https://reitaisai.com/rts19/"),
    "RTS22": ("2025-05-05", "2025-05-05", "https://reitaisai.com/rts22/"),
    "RTS23": ("2026-05-04", "2026-05-04", "https://reitaisai.com/rts23/"),
}
EVENT_TAG = r"(?:M3[-‐‑– ]?[0-9]{1,3}|C[-‐‑– ]?[0-9]{2,3}|(?:ARTS|RTS|THK|TMKS)[0-9]{1,2}|RTSSP[0-9]?)"
KEYS = {
    "album": "album", "title": "album", "artist": "artist", "albumartist": "album_artist",
    "publisher": "publisher", "label": "publisher", "recordlabel": "publisher",
    "catalog": "catalog_id", "catalogid": "catalog_id", "catalognumber": "catalog_id",
    "catalogno": "catalog_id", "catno": "catalog_id", "releasedate": "release_date",
}
KEY_VALUE = re.compile(r"(?m)^\s*([A-Za-z][A-Za-z ._#-]{0,29})\s*[:=：]\s*([^\r\n]+)")


REFERRER_POLICIES = frozenset({"no-referrer", "no-referrer-when-downgrade", "origin",
                              "origin-when-cross-origin", "same-origin", "strict-origin",
                              "strict-origin-when-cross-origin", "unsafe-url"})
HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"


def referrer_policy(value):
    return next((token.strip() for token in reversed(value.split(",")) if token.strip() in REFERRER_POLICIES), "")


def document_policy(page, headers):
    policies = page.xpath('//meta[translate(@name,"REF","ref")="referrer"]/@content')
    return referrer_policy(",".join([headers.get("Referrer-Policy", ""), *policies]))


def navigation_referrer(source, target, policy=""):
    if not source or policy == "no-referrer":
        return None
    origin, destination = urlsplit(source), urlsplit(target)
    if origin.scheme not in ("http", "https") or not origin.hostname or origin.username is not None:
        return None
    downgrade = origin.scheme == "https" and destination.scheme != "https"
    if downgrade and policy not in ("origin", "origin-when-cross-origin", "unsafe-url"):
        return None
    host = origin.hostname
    authority = f"[{host}]" if ":" in host else host
    default_port = 443 if origin.scheme == "https" else 80
    if origin.port not in (None, default_port):
        authority += f":{origin.port}"
    same_origin = (origin.scheme == destination.scheme and host == destination.hostname
                   and (origin.port or default_port) == (destination.port or default_port))
    if policy == "same-origin" and not same_origin:
        return None
    full_url = policy not in ("origin", "strict-origin") and (
        same_origin or policy in ("unsafe-url", "no-referrer-when-downgrade"))
    path = (origin.path or "/") if full_url else "/"
    query = f"?{origin.query}" if full_url and origin.query else ""
    return f"{origin.scheme}://{authority}{path}{query}"


class NavigationRedirect(HTTPRedirectHandler):
    pass  # The installed class is an inert type marker; HTTP lives in C++.
    @standalone_only
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Referer")
            policy = referrer_policy(headers.get("Referrer-Policy", "")) or getattr(req, "referrer_policy", "")
            redirected.referrer_policy = policy
            if referer := navigation_referrer(req.get_header("Referer"), newurl, policy):
                redirected.add_header("Referer", referer)
        return redirected


class NoRedirect(HTTPRedirectHandler):
    pass  # The installed class is an inert type marker; HTTP lives in C++.
    @standalone_only
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Http:
    pass  # The installed class is an inert type marker; HTTP lives in C++.
    @standalone_only
    def __init__(self, redirect_handler=None):
        cookies = CookieJar()
        self.opener = build_opener(redirect_handler or NavigationRedirect(), HTTPCookieProcessor(cookies))
        self.no_redirect = build_opener(NoRedirect(), HTTPCookieProcessor(cookies))
        self.log = []

    @standalone_only
    def get(self, url, *, headers=None, data=None, redirects=True, cap=HTML_LIMIT, timeout=12, policy=""):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None:
            raise ValueError("Expected an HTTP(S) URL without credentials")
        request = Request(url, data, {"User-Agent": UA, "Accept-Encoding": "identity", **(headers or {})})
        request.referrer_policy = policy
        if source := request.get_header("Referer"):
            request.remove_header("Referer")
            if referer := navigation_referrer(source, url, policy):
                request.add_header("Referer", referer)
        result = None
        start = time.monotonic()
        transferred = 0
        try:
            opener = self.opener if redirects else self.no_redirect
            try:
                result = opener.open(request, timeout=timeout)
            except HTTPError as exc:
                result = exc
                if redirects or not 300 <= exc.code < 400:
                    raise
            if headers and "Range" in headers and result.status != 206:
                raise ValueError(f"Range request returned HTTP {result.status}; response body skipped")
            if cap == 0:
                return result.headers, b"", result.geturl()
            if int(result.headers.get("Content-Length", 0)) > cap:
                raise ValueError(f"Response exceeds the {cap}-byte limit")
            body = result.read(cap + 1)
            transferred = len(body)
            if transferred > cap:
                raise ValueError(f"Response exceeds the {cap}-byte limit")
            return result.headers, body, result.geturl()
        finally:
            if result is not None:
                result.close()
            # Omit paths and query strings, including temporary CDN tokens.
            self.log.append({
                "host": parsed.hostname, "method": request.get_method(),
                "range": (headers or {}).get("Range"), "status": getattr(result, "status", None),
                "body_bytes": transferred, "ms": round(1000 * (time.monotonic() - start)),
            })


@standalone_only
def clean(element):
    return " ".join(element.text_content().split()) if element is not None else ""


@standalone_only
def first(elements):
    return next(iter(elements), None)


@standalone_only
def class_token(name):
    return f'contains(concat(" ", normalize-space(@class), " "), " {name} ")'


def ds_url(**params):
    return DS + "?" + urlencode(params)


def normalize(value):
    return unicodedata.normalize("NFKC", value).strip()


def event(tag):
    key = re.sub(r"\s+", "", normalize(tag).upper())
    key = re.sub(r"^M3[-‐‑–]?(\d+)$", r"M3-\1", key)
    key = re.sub(r"^C[-‐‑–](\d+)$", r"C\1", key)
    if not re.fullmatch(EVENT_TAG, key):
        return None
    start, end, source = EVENTS.get(key, (None, None, None))
    return {"id": key, "tag": tag, "start": start, "end": end, "date_source": source}


@standalone_only
def ds_date(value):
    value = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", value).replace(".", "")
    if not value:
        return None
    try:
        return datetime.strptime(value, "%b %d, %Y").date().isoformat()
    except ValueError:
        return value


def iso_release_date(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([0-9]{4})[-/.]([0-9]{1,2})[-/.]([0-9]{1,2})", value.strip())
    if match:
        try:
            return datetime(*(int(part) for part in match.groups())).date().isoformat()
        except ValueError:
            pass
    return None


def catalog_number_only(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r"[‐‑–]", "-", value.strip())
    return value.upper() if re.fullmatch(CATNO, value, re.I | re.ASCII) and not event(value) else None


@standalone_only
def release_row(identifier, title, artist, tags, advertised):
    return {
        "id": int(identifier), "url": ds_url(p="page", type=1, id=identifier),
        "album": title, "artist": artist or None, "album_artist": None,
        "publisher": None, "catalog_id": None, "release_date": None,
        "tags": tags, "events": [item for tag in tags if (item := event(tag))],
        "technical": {"advertised_format": advertised or None},
        "evidence": {"album": "DoujinStyle", "artist": "DoujinStyle listing credit"},
    }


@standalone_only
def parse_listing(body, limit, include_forms=False):
    # The site's HTTP header and bytes are UTF-8, despite its UTF-16 meta tag.
    page = html.fromstring(body, parser=html.HTMLParser(encoding="UTF-8"))
    rows = []
    for card in page.xpath(f"//*[{class_token('gridBox')}]"):
        details = first(card.xpath(f".//*[{class_token('gridDetails')}]"))
        if details is None:
            continue
        title = first(details.xpath('.//a[contains(@href,"p=page")]'))
        if title is None:
            continue
        params = parse_qs(urlsplit(title.get("href")).query)
        identifier = params.get("id", [""])[0]
        if params.get("type") != ["1"] or not identifier.isdecimal():
            continue
        artist = clean(first(details.xpath('.//a[contains(@href,"type=artist")]')))
        tags = [clean(link) for link in details.xpath('.//a[contains(@href,"type=tags")]')]
        advertised = clean(first(card.xpath('.//*[@title and starts-with(@title,"Music Format:")]')))
        row = release_row(identifier, clean(title), artist, tags, advertised)
        if include_forms:
            row["download_forms"] = download_forms(card.xpath('.//form[.//*[@name="download_link"]]'), identifier)
        rows.append(row)
    count = re.search(rb"Total Results Found:\s*([\d,]+)", body)
    total = int(count[1].replace(b",", b"")) if count else None
    if not rows and page.xpath("//mainbar//*[normalize-space(text())='No Matches Found.']"):
        total = 0
    if not rows and total != 0:
        raise ValueError("DoujinStyle returned no recognizable catalog cards")
    result = {"total_results": total, "rows_on_page": len(rows), "releases": rows[:limit]}
    if icon := site_icon_url(page):
        result["site_icon"] = icon
    return result


@standalone_only
def site_icon_url(page):
    for link in page.xpath("//head/link[@href and contains(concat(' ', normalize-space(@rel), ' '), ' icon ')]"):
        candidate = urlsplit(urljoin(DS, link.get("href")))
        if candidate.scheme == "https" and candidate.hostname in {"doujinstyle.com", "www.doujinstyle.com"} and (
            candidate.username is None and candidate.port in (None, 443)
        ):
            return candidate._replace(fragment="").geturl()
    return None


@standalone_only
def parse_popular(body):
    page = html.fromstring(body, parser=html.HTMLParser(encoding="UTF-8"))
    ranges = {"this month": "month", "all-time": "all", "all time": "all"}
    headings = {node for node in page.xpath("//h1|//h2|//h3|//h4|//h5|//h6") if clean(node).casefold() in ranges}
    if len(headings) < 2:
        headings = {node for node in page.xpath("//*[not(*)]") if clean(node).casefold() in ranges}

    charts = {"month": [], "all": []}
    active = None
    for node in page.iter():
        if node in headings:
            active = ranges[clean(node).casefold()]
        if active is None or node.tag != "a" or len(charts[active]) == 10:
            continue
        target = urlsplit(urljoin(DS, node.get("href") or ""))
        params = parse_qs(target.query)
        identifier = params.get("id", [""])[0]
        title = clean(node)
        if (target.hostname not in {"doujinstyle.com", "www.doujinstyle.com"} or params.get("p") != ["page"]
                or params.get("type") != ["1"] or not identifier.isascii() or not identifier.isdecimal() or not title):
            continue
        for parent in node.iterancestors():
            if len(parent.xpath('.//a[contains(@href,"p=page") and contains(@href,"type=1")]')) > 1:
                break
            parts = [part.strip() for part in parent.itertext() if part.strip()]
            amount = re.search(r"\b([\d,]+)\s+downloads?\b", " ".join(parts), re.I)
            if not amount:
                continue
            offset = next((i + 1 for i, part in enumerate(parts) if part == title), len(parts))
            artist = next((part for part in parts[offset:] if "download" not in part.casefold()
                           and not re.fullmatch(r"[\d,]+", part)), "")
            row = release_row(identifier, title, artist, [], None)
            row["chart_rank"] = len(charts[active]) + 1
            row["chart_downloads"] = int(amount[1].replace(",", ""))
            if not any(previous["id"] == row["id"] for previous in charts[active]):
                charts[active].append(row)
            break
    if not all(charts.values()):
        raise ValueError("DoujinStyle did not return both recognizable popularity charts")
    return charts, site_icon_url(page)


@standalone_only
def parse_detail(body, identifier, document_url=None, response_headers=None):
    page = html.fromstring(body, parser=html.HTMLParser(encoding="UTF-8"))
    title = clean(first(page.xpath("//mainbar//h2")))
    if not title:
        raise ValueError("DoujinStyle did not return a recognizable release page")
    values = {}
    for label in page.xpath(f"//mainbar//*[{class_token('pageSpan1')}]"):
        sibling = label.getnext()
        if sibling is not None and "pageSpan2" in sibling.get("class", "").split():
            values[clean(label).rstrip(":").lower()] = sibling
    value = lambda key: clean(values.get(key))
    tags = [clean(link) for link in values["tags"].xpath(".//a")] if "tags" in values else []
    row = release_row(identifier, title, value("artist"), tags, value("format"))
    row["listed_at"] = ds_date(value("date added"))
    row["updated_at"] = ds_date(value("last updated"))
    for name, node in values.items():
        field = KEYS.get(re.sub(r"[\W_]+", "", name))
        if field in {"album_artist", "publisher", "catalog_id", "release_date"} and clean(node):
            row[field] = clean(node)
            row["evidence"][field] = "DoujinStyle detail"
    document_url = document_url or row["url"]
    policy = document_policy(page, response_headers or {})
    forms = download_forms(page.xpath('//mainbar//form[.//*[@name="download_link"]]'), identifier,
                           document_url, policy)
    row["download_offer"] = bool(forms)
    return row, forms


@standalone_only
def download_fields(form):
    if form is None:
        return None
    return {node.get("name"): node.get("value", "") for node in form.xpath('.//input[@type="hidden"]')
            if node.get("name") in {"id", "type", "source"}}


@standalone_only
def download_forms(forms, identifier, document_url=None, policy=""):
    slots = {}
    for form in forms:
        fields = download_fields(form)
        slot = fields.get("source", "0")
        if fields.get("id") == str(identifier) and fields.get("type") == "1" and slot.isascii() and slot.isdecimal():
            if document_url:
                button = first(form.xpath('.//*[@name="download_link" and not(@disabled)]'))
                if button is None:
                    continue
                action = button.get("formaction") or form.get("action") or document_url
                fields.update(action=urljoin(document_url, action), document_url=document_url,
                              method=(button.get("formmethod") or form.get("method") or "get").lower(),
                              button_value=button.get("value", ""), referrer_policy=policy)
            slots.setdefault(int(slot), fields)
    return [slots[key] for key in sorted(slots)]


@standalone_only
def offer_source(fields):
    slot = int(fields.get("source", "0"))
    return f"doujinstyle:{slot}" if slot else "doujinstyle"


@standalone_only
def describe_offer(url):
    parsed = urlsplit(url)
    path_parts = [unquote(part) for part in parsed.path.split("/") if part]
    filename = next((part for part in reversed(path_parts)
                     if PurePosixPath(part).suffix.lower() in {".zip", ".rar", ".7z", ".gz"}), None)
    offer = {"host": parsed.hostname, "filename": filename, "format_hint": (
        PurePosixPath(filename).suffix.lower()[1:] if filename else None)}
    if filename and (candidate := catalog_hint(filename)):
        offer["catalog_id_candidate"] = candidate
    return offer


@standalone_only
def resolve_offer(http, row, fields):
    if fields is None:
        raise ValueError("No download offer was found")
    if fields.get("id") != str(row["id"]) or fields.get("type") != "1":
        raise ValueError("Unexpected DoujinStyle download form")
    action = fields.get("action", DS)
    source = fields.get("document_url", row["url"])
    origin = navigation_referrer(source, source, "origin")
    if (not origin or navigation_referrer(action, action, "origin") != origin or
            fields.get("method", "post") != "post"):
        raise ValueError("Unexpected DoujinStyle download form destination or method")
    payload = {key: fields[key] for key in ("id", "type", "source") if key in fields}
    payload["download_link"] = fields.get("button_value", "")
    policy = fields.get("referrer_policy", "")
    headers, _, _ = http.get(action, data=urlencode(payload).encode(), redirects=False, cap=0, policy=policy,
                             headers={"Referer": source, "Origin": "null" if policy == "no-referrer" else origin.rstrip("/"),
                                      "Accept": HTML_ACCEPT, "Content-Type": "application/x-www-form-urlencoded"})
    location = headers.get("Location")
    if not location:
        raise ValueError("The download form did not return a host redirect")
    url = urlsplit(urljoin(action, location))
    if url.scheme != "https" or not url.hostname or url.hostname.removeprefix("www.") == "doujinstyle.com":
        raise ValueError("Unexpected download offer destination")
    # http.client represents header bytes as Latin-1. Quote the original bytes
    # so that raw UTF-8 in Location is not encoded as UTF-8 a second time.
    target = url._replace(
        path=quote(url.path, safe="/%:@!$&'()*+,;=", encoding="latin-1"),
        query=quote(url.query, safe="/%:@!$&'()*+,;=?", encoding="latin-1"),
        fragment=quote(url.fragment, safe="/%:@!$&'()*+,;=?", encoding="latin-1"),
    ).geturl()
    return describe_offer(target), target


@standalone_only
def resolve_offers(http, row, forms):
    results, errors = [], {}
    for fields in forms:
        source = offer_source(fields)
        try:
            offer, url = resolve_offer(http, row, fields)
            results.append((source, offer, url))
        except (ValueError, OSError) as exc:
            errors[source] = str(exc)
    if not forms:
        errors["doujinstyle"] = "No download offer was found"
    return results, errors


def catalog_hint(name, *, bracketed_only=False):
    pattern = rf"\[\s*({CATNO})\s*\]"
    pattern = "^" + pattern if bracketed_only else pattern + rf"|^({CATNO})(?=$|[\s._])"
    match = re.search(pattern, normalize(name), re.I)
    return next((value for value in match.groups() if value), None) if match else None


def add_catalog_candidate(evidence, candidate, source, name):
    if candidate:
        key = re.sub(r"[‐‑–]", "-", normalize(candidate)).upper()
        evidence.setdefault(key, []).append({"source": source, "name": name})


@standalone_only
def release_catalog_candidates(row):
    offer = row.get("offer") or {}
    evidence = {}
    add_catalog_candidate(evidence, offer.get("catalog_id_candidate"), "offer_filename", offer.get("filename"))
    for candidate, sources in (row.get("archive") or {}).get("catalog_id_candidate_evidence", {}).items():
        evidence.setdefault(candidate, []).extend(sources)
    if evidence:
        row["catalog_id_candidate_evidence"] = evidence
        row["catalog_id_candidate"] = next(iter(evidence)) if len(evidence) == 1 else None


@standalone_only
def dojin_info(http, url, row):
    url_parts = urlsplit(url)
    if url_parts.hostname not in ("www.dojin-music.info", "dojin-music.info") or not re.fullmatch(r"/cd/\d+/?", url_parts.path):
        raise ValueError("--dojin-info expects a dojin-music.info CD URL")
    _, body, _ = http.get(url)
    page = html.fromstring(body)
    payload = first(page.xpath('//script[@data-page="app" and @type="application/json"]/text()'))
    if payload is None:
        raise ValueError("Missing structured release data from 同人音楽info")
    props = json.loads(payload)["props"]
    cd = props["cd"]
    spaces = [props[name] for name in ("eventSpace", "eventSpace2") if props.get(name)]
    date = str(cd.get("day_sale") or "")
    date = datetime.strptime(date, "%Y%m%d").date().isoformat() if re.fullmatch(r"\d{8}", date) else None
    return {"url": url, "album": cd.get("name"), "circles": [c["name"] for c in props["circle"]],
            "distribution_date": date, "catalog_id_candidate": catalog_hint(
                PurePosixPath(urlsplit(cd.get("link_url") or "").path).name),
            "track_count": len(props.get("songList") or []) or None, "creator_url": cd.get("link_url"),
            "events": [{"name": entry.get("name"), "date": entry.get("date"),
                        "booth": f"{entry.get('space_head') or ''}-{entry.get('space_number') or ''}".strip("-") or None}
                       for entry in spaces],
            "title_matches": normalize(cd.get("name") or "").casefold() == normalize(row["album"]).casefold()}


@standalone_only
def creator_release(http, url, row):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ("umsoundz.net", "www.umsoundz.net") or not parsed.path.startswith("/releases/"):
        raise ValueError("The prototype's creator adapter expects an Ultramarine Soundz release URL")
    _, body, _ = http.get(url)
    page = html.fromstring(body)
    title = clean(first(page.xpath("//main//h1")))
    circle = clean(first(page.xpath(f"//main//*[{class_token('org')}]")))
    title_matches = bool(title) and normalize(title).casefold() == normalize(row["album"]).casefold()
    circle_matches = bool(circle and row["artist"]) and normalize(circle).casefold() == normalize(row["artist"]).casefold()
    details = {clean(el): clean(el.getnext()) for el in page.xpath("//main//dt")}
    raw_date = re.search(r"\b\d{4}/\d{1,2}/\d{1,2}\b", details.get("Release", ""))
    date = datetime.strptime(raw_date[0], "%Y/%m/%d").date().isoformat() if raw_date else None
    catalog = clean(first(page.xpath(f"//main//*[{class_token('catalogNumber')}]")))
    tracks = [
        {"number": n, "title": clean(first(track.xpath(f".//*[{class_token('track-title')}]"))),
         "artist": clean(first(track.xpath(f".//*[{class_token('track-artist')}]"))) or None}
        for n, track in enumerate(page.xpath(f"//*[@id='tracklist']//*[{class_token('track-info')}]"), 1)
    ]
    return {
        "url": url, "album": title, "publishing_circle": circle or None, "catalog_id": catalog or None,
        "release_date": date, "release_text": details.get("Release"), "physical_medium": details.get("Specification"),
        "tracks": tracks, "title_matches": title_matches, "circle_matches": circle_matches,
    }


@standalone_only
def musicbrainz(http, row, catno=None):
    def term(value):
        return '"' + re.sub(r'([\\"])', r'\\\1', value) + '"'
    query = f"catno:{term(catno)}" if catno else f"release:{term(row['album'])}"
    if row["artist"] and not catno:
        query += f" AND artist:{term(row['artist'])}"
    _, body, _ = http.get(MB + "ws/2/release?" + urlencode({"query": query, "fmt": "json", "limit": 5}),
                          headers={"Accept": "application/json"})
    if not body.lstrip().startswith(b"{"):
        raise ValueError("MusicBrainz returned a non-JSON response")
    payload = json.loads(body)
    return [{"id": item["id"], "url": MB + "release/" + item["id"], "album": item.get("title"),
             "album_artist": item.get("artist-credit"), "release_date": item.get("date"),
             "labels": [{"publisher": (label.get("label") or {}).get("name"),
                         "catalog_id": label.get("catalog-number")} for label in item.get("label-info", [])],
             "media": item.get("media"), "track_count": item.get("track-count"), "score": item.get("score")}
            for item in payload.get("releases", [])]


ZIP_FOOTER_WINDOW = 22 + 65535


@standalone_only
class ArchiveRangeResponseError(ValueError):
    pass


@standalone_only
class RangedObject:
    """An immutable HTTP object with shared byte spans and independent cursors."""

    def __init__(self, http, url, budget, max_requests=24, *, referrer=None, policy="", suffix_bytes=8192, native_mediafire=None):
        self.http, self.url, self.budget = http, url, budget
        self.referrer = referrer
        self.policy = policy
        self.native_mediafire = native_mediafire
        self.max_requests = max_requests
        self.fetched = self.requests = 0
        self.chunks = {}
        self.lock = Lock()
        self.size = None
        self.etag = None
        self.modified = None
        self.filename = None
        self.fetch(None, min(suffix_bytes, budget))

    def fetch(self, start, length):
        if length > self.budget - self.fetched or self.requests >= self.max_requests:
            raise ValueError("Archive inspection byte/request budget exhausted")
        self.requests += 1
        validator = self.etag or self.modified
        if self.native_mediafire is not None:
            response, body = self.native_mediafire.mediafire_range(
                self.url, start, length, self.referrer, self.policy, validator)
        else:
            range_value = f"bytes=-{length}" if start is None else f"bytes={start}-{start + length - 1}"
            headers = {"Range": range_value}
            if self.referrer:
                headers["Referer"] = self.referrer
            if validator:
                headers["If-Range"] = validator
            response, body, _ = self.http.get(self.url, headers=headers, cap=length, policy=self.policy)
        self.fetched += len(body)
        span = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.get("Content-Range", ""))
        if not span or response.get("Content-Encoding", "identity").lower() != "identity":
            raise ArchiveRangeResponseError("Invalid HTTP byte-range response")
        lower, upper, total = map(int, span.groups())
        expected_start = max(0, total - length) if start is None else start
        if lower != expected_start or upper != min(lower + length, total) - 1 or upper - lower + 1 != len(body):
            raise ArchiveRangeResponseError("HTTP response does not match the requested byte range")
        etag, modified = response.get("ETag"), response.get("Last-Modified")
        if self.size is None:
            self.size = total
            self.etag = etag if etag and not etag.startswith("W/") else None
            self.modified = modified
            self.filename = response.get_filename()
        elif total != self.size or (self.etag and etag and etag != self.etag) or (
            not self.etag and self.modified and modified and modified != self.modified
        ):
            raise ArchiveRangeResponseError("Remote archive changed during inspection")
        self.chunks[lower] = body

    def read_at(self, offset, length):
        with self.lock:
            return self._read_at(offset, length)

    def _read_at(self, offset, length):
        length = min(length, max(0, self.size - offset))
        parts = []
        end = offset + length
        while offset < end:
            hit = next(((begin, content) for begin, content in self.chunks.items()
                        if begin <= offset < begin + len(content)), None)
            if hit is None:
                next_cached = min((begin for begin in self.chunks if begin > offset), default=self.size)
                span = min(next_cached - offset, max(8192, end - offset),
                           self.budget - self.fetched)
                if span < 1:
                    raise ValueError("Archive inspection byte budget exhausted")
                self.fetch(offset, span)
                continue
            begin, content = hit
            block = content[offset - begin:end - begin]
            parts.append(block)
            offset += len(block)
        return b"".join(parts)

    def cursor(self):
        return RangeCursor(self)


@standalone_only
class RangeCursor(io.RawIOBase):
    def __init__(self, resource):
        super().__init__()
        self.resource = resource
        self.offset = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.offset

    def seek(self, offset, whence=io.SEEK_SET):
        if whence not in (io.SEEK_SET, io.SEEK_CUR, io.SEEK_END):
            raise ValueError("Invalid seek mode")
        position = (0, self.offset, self.resource.size)[whence] + offset
        if position < 0:
            raise OSError("Negative seek")
        self.offset = position
        return position

    def read(self, size=-1):
        if size < 0:
            size = max(0, self.resource.size - self.offset)
        data = self.resource.read_at(self.offset, size)
        self.offset += len(data)
        return data


@standalone_only
def archive_destination(http, url, referrer=None):
    host = urlsplit(url).hostname or ""
    if host not in MEDIAFIRE_SHARE_HOSTS:
        return url, referrer, ""
    headers = {"Accept": HTML_ACCEPT}
    if referrer:
        headers["Referer"] = referrer
    response, body, base = http.get(url, headers=headers)
    page = html.fromstring(body)
    link = first(page.xpath('//a[@id="downloadButton" and @href]'))
    if link is None:
        raise ValueError(f"MediaFire direct file link unavailable ({clean(first(page.xpath('//title'))) or 'no page title'})")
    html_base = first(page.xpath('//base[@href]/@href'))
    target = urljoin(urljoin(base, html_base) if html_base else base, link.get("href"))
    parsed = urlsplit(target)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".mediafire.com"):
        raise ValueError("Unexpected MediaFire download endpoint")
    policy = ("no-referrer" if "noreferrer" in link.get("rel", "").lower().split() else
              referrer_policy(link.get("referrerpolicy", "")) or document_policy(page, response))
    return target, base, policy


@standalone_only
def decode_note(data):
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("cp437")


@standalone_only
def sidecar_values(name, data):
    text = decode_note(data)
    hints = {}
    if name.lower().endswith(".cue"):
        commands = re.split(r"(?im)^\s*(?:FILE|TRACK)\s", text, maxsplit=1)[0]
        for cue_key, tag in (("TITLE", "album"), ("PERFORMER", "album_artist")):
            value = re.search(rf'^\s*{cue_key}\s+"([^"]+)"', commands, re.M | re.I)
            if value:
                hints[tag] = value[1]
    for match in KEY_VALUE.finditer(text):
        key = KEYS.get(re.sub(r"[\W_]+", "", match[1].lower()))
        if key:
            hints[key] = match[2].strip()
    return hints


@standalone_only
def flac_info(header):
    if len(header) != 42 or header[:4] != b"fLaC" or header[4] & 0x7F or int.from_bytes(header[5:8], "big") != 34:
        return None
    packed = struct.unpack(">Q", header[18:26])[0]
    rate = packed >> 44
    samples = packed & ((1 << 36) - 1)
    if rate == 0:
        return None
    return {"codec": "FLAC", "sample_rate_hz": rate, "channels": (packed >> 41 & 7) + 1,
            "bits_per_sample": (packed >> 36 & 31) + 1,
            "duration_seconds": round(samples / rate, 3) if samples else None}


@standalone_only
def vorbis_comment_tags(data):
    values = {}
    offset = 0

    def word():
        nonlocal offset
        if offset + 4 > len(data):
            raise ValueError("Truncated FLAC comment length")
        length = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        return length

    def string():
        nonlocal offset
        length = word()
        if offset + length > len(data):
            raise ValueError("Truncated FLAC comment")
        value = data[offset:offset + length].decode("utf-8", errors="replace")
        offset += length
        return value

    string()  # Encoder vendor
    count = word()
    if count > (len(data) - offset) // 4:
        raise ValueError("Invalid FLAC comment count")
    for _ in range(count):
        key, separator, value = string().partition("=")
        if separator and key and value:
            values.setdefault(key.upper(), []).append(value)
    return values


@standalone_only
def sample_flac_member(stream):
    header = stream.read(42)
    metadata = flac_info(header)
    sample = {"metadata": metadata, "tags": {}}
    if metadata is None or header[4] & 128:
        return sample
    remaining = 32768
    while remaining >= 4:
        try:
            block = stream.read(4)
            if len(block) != 4:
                raise ValueError("Truncated FLAC metadata block")
            size = int.from_bytes(block[1:], "big")
            if size > remaining - 4:
                raise ValueError("FLAC comment lookup reached the 32 KiB metadata budget")
            data = stream.read(size)
            if len(data) != size:
                raise ValueError("Truncated FLAC metadata payload")
            if block[0] & 127 == 4:
                sample["tags"] = vorbis_comment_tags(data)
                break
            if block[0] & 128:
                break
            remaining -= 4 + size
        except (ValueError, OSError) as exc:
            sample["tags_status"] = str(exc)
            break
    return sample


@standalone_only
def archive_sidecar(name):
    path = PurePosixPath(name.replace("\\", "/"))
    return path.name.startswith("._") or any(part.casefold() == "__macosx" for part in path.parts)


@standalone_only
def member_entry(member):
    from native.archive_zip import Member
    suffix = PurePosixPath(member.filename.replace("\\", "/")).suffix.lower()
    kind = "archive_metadata" if archive_sidecar(member.filename) else next(
        (kind for kind, extensions in MEMBER_TYPES.items() if suffix in extensions), "unknown")
    record = {"path": member.filename, "suffix": suffix or None, "kind": kind,
              "compressed_bytes": member.compress_size, "uncompressed_bytes": member.file_size,
              "zip_method": member.compress_type, "zip_header_offset": member.header_offset,
              "crc32": f"{member.CRC:08x}", "encrypted": bool(member.flag_bits & 1)}
    if isinstance(member, Member) and (member.raw_name != member.filename.encode("utf-8") or member.name_warning):
        record["zip_name"] = {"raw": member.raw_name.hex(), "encoding": member.encoding}
        if member.name_warning:
            record["zip_name"]["warning"] = "Invalid or conflicting ZIP Unicode filename evidence"
        if member.alternate_name:
            record["zip_name"]["legacy_unicode_path"] = member.alternate_name
    return record


@standalone_only
def cache_zip_front_cover(archive, entries, source, sink, limit):
    from native.archive_zip import read_member
    names = {"front", "frontcover", "cover", "folder", "jacket", "表紙"}
    candidates = []
    remaining = source.budget - source.fetched
    for entry in entries:
        if archive_sidecar(entry.filename):
            continue
        path = PurePosixPath(entry.filename.replace("\\", "/"))
        if (path.suffix.lower() in {".jpg", ".jpeg", ".png"} and
                re.sub(r"[\W_]+", "", normalize(path.stem).casefold()) in names and
                not entry.flag_bits & 1 and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) and
                entry.file_size <= limit and entry.compress_size <= remaining):
            candidates.append((len(path.parts), entry.file_size, entry.filename, entry))
    if not candidates or source.requests >= source.max_requests:
        return None
    member = min(candidates, key=lambda item: item[:3])[-1]
    try:
        data = read_member(archive, member, limit + 1)
        if len(data) <= limit and data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
            sink(data)
            return member.filename
    except (ValueError, RuntimeError, OSError, zipfile.BadZipFile):
        pass
    return None


@standalone_only
def compatibility_audio_prefix(data, suffix, size):
    # The standalone prototype retains the reference parser for parity fixtures.
    # Production never embeds it; if its native helper fails, leave the probe
    # explicitly incomplete so the next indexing run can repair its metadata.
    # Never import a same-named module from the working directory or site-packages
    # when this code runs inside the signed component's installed zipapp.
    from zipimport import zipimporter
    if isinstance(__loader__, zipimporter):
        return {"prefix_status": "native audio prefix helper unavailable; retry indexing"}
    try:
        from audio_probe import audio_prefix
    except ModuleNotFoundError as exc:
        if exc.name != "audio_probe":
            raise
        return {"prefix_status": "native audio prefix helper unavailable; retry indexing"}
    return audio_prefix(data, suffix, size, flac_info, vorbis_comment_tags)


@standalone_only
def inspect_zip(http, url, budget, sample_flac=False, directory_only=False, *,
                header_probes=False, max_requests=24, cover_sink=None, referrer=None, zip_worker=None, native_mediafire=None):
    from native.archive_providers import ARCHIVE_PROVIDERS
    from native.archive_zip import CatalogZip, MediaFireZip, Member, read_member
    from catalog_cache import host_family
    provider = ARCHIVE_PROVIDERS.get(host_family(urlsplit(url).hostname or ""))
    # Every native provider uses the SAME validated C++ ZIP parser and inflater.
    # Public MEGA file keys never flow through Python's Windows crypto backend here.
    native_mega = (provider is not None and host_family(urlsplit(url).hostname or "") == "mega.nz"
                   and zip_worker is not None and getattr(zip_worker, "host_zlib", None) is not None
                   and os.name == "nt" and directory_only and not sample_flac)
    fast_zip = None
    inventory_only = directory_only and not sample_flac and not header_probes and cover_sink is None
    suffix_bytes = ZIP_FOOTER_WINDOW if inventory_only and budget >= 2 * ZIP_FOOTER_WINDOW else 8192
    if native_mega:
        view = zip_worker.mega_zip_open(url, budget, max_requests, suffix_bytes)
        if view is not None and view.get("format") != "zip":
            # Solid 7z/RAR and malformed ZIP are classified by bounded native
            # magic-byte reads.  Never fetch the archive into Python to guess.
            filename = view.get("filename")
            evidence = {}
            if filename:
                leaf = PurePosixPath(filename.replace("\\", "/")).name
                add_catalog_candidate(evidence, catalog_hint(leaf), "archive_filename", leaf)
            return {"archive_bytes": view["size"], "response_filename": filename,
                    "format": view["format"], "inspection": "No ZIP directory available",
                    "catalog_id_candidates": sorted(evidence),
                    "catalog_id_candidate_evidence": evidence,
                    "archive_http": {"requests": view["requests"], "body_bytes": view["fetched"],
                                     "validator": None, "etag": None, "last_modified": None,
                                     "transport": "winhttp", "mega_mac": "not_verified_for_range_probes"}}
        if view is not None:
            fast_zip = MediaFireZip(zip_worker, view, budget, max_requests, provider="mega")
    if fast_zip is None and provider is not None and host_family(urlsplit(url).hostname or "") == "mega.nz" \
            and provider.open_ranges is None:
        # Installed zipapp has no Python MEGA implementation. Never try to
        # reach a source-only module via import paths or perform unauthenticated HTTP.
        raise RuntimeError("Native MEGA reader unavailable; retry archive inspection")
    if fast_zip is None and provider and provider.open_ranges:
        http = provider.open_ranges(url)
        referrer = None
    if fast_zip is None:
        if (urlsplit(url).hostname or "") not in MEDIAFIRE_SHARE_HOSTS:
            native_mediafire = None
        if native_mediafire is None:
            resolved, archive_referrer, policy = archive_destination(http, url, referrer)
        else:
            resolved, archive_referrer, policy = native_mediafire.mediafire_resolve(url, referrer)
        if native_mediafire is not None and directory_only and not sample_flac:
            view = native_mediafire.mediafire_zip_open(resolved, archive_referrer, policy,
                                                       budget, max_requests, suffix_bytes)
            if view is not None:
                fast_zip = MediaFireZip(native_mediafire, view, budget, max_requests)
    else:
        resolved, archive_referrer, policy = url, None, ""
    source = fast_zip or RangedObject(http, resolved, budget, max_requests, referrer=archive_referrer,
                                     policy=policy, suffix_bytes=suffix_bytes, native_mediafire=native_mediafire)
    report = {"archive_bytes": source.size, "response_filename": source.filename, "format": None}
    catalog_evidence = {}
    if source.filename:
        filename = PurePosixPath(source.filename.replace("\\", "/")).name
        add_catalog_candidate(catalog_evidence, catalog_hint(filename), "archive_filename", filename)
    native_zip = fast_zip
    if native_zip is None and zip_worker is not None and not sample_flac:
        native_zip = CatalogZip.open(source, zip_worker, min(suffix_bytes, budget))
    try:
        archive_context = zipfile.ZipFile(source.cursor()) if native_zip is None else nullcontext(native_zip)
    except zipfile.BadZipFile:
        magic = source.read_at(0, 8)
        report["format"] = next((kind for signature, kind in (
            (b"Rar!\x1a\x07", "rar"), (b"7z\xbc\xaf\x27\x1c", "7z"), (b"\x1f\x8b", "gzip"),
            (b"PK\x03\x04", "zip_invalid_or_incomplete"),
        ) if magic.startswith(signature)), "unknown")
        report["inspection"] = "No ZIP directory available"
    else:
        with archive_context as archive:
            report["format"] = "zip"
            members = archive.infolist()
            if zip_worker is not None:
                projected = zip_worker.zip_inventory(members)
            else:
                # The installed zipapp cannot import this source-only reference.
                # Missing native inventory must remain incomplete for retry.
                from zipimport import zipimporter
                if isinstance(__loader__, zipimporter):
                    raise RuntimeError("Native ZIP inventory helper unavailable; retry indexing")
                from native.zip_inventory_reference import project as reference_zip_inventory
                projected = reference_zip_inventory(members)
            if (not isinstance(projected, dict) or len(members) > 32768 or
                    not all(isinstance(projected.get(key), list) for key in
                            ("entry_indices", "playable_indices", "sidecar_candidates", "roots"))):
                raise ValueError("Native ZIP inventory returned an incomplete projection")
            entries = [members[i] for i in projected["entry_indices"]]
            playable = [members[i] for i in projected["playable_indices"]]
            selected_notes = [(0, 0, members[i]) for i in projected["sidecar_candidates"]]
            for root in projected["roots"]:
                add_catalog_candidate(catalog_evidence, catalog_hint(root, bracketed_only=True), "zip_root", root)
            for key in ("members", "audio_members", "audio", "inventory", "member_types",
                        "unknown_suffixes", "sidecar_candidates"):
                report[key] = projected[key] if key != "sidecar_candidates" else [
                    member.filename for _, _, member in selected_notes]
            report["sidecars"] = []
            if header_probes:
                report["artwork"] = projected["artwork"]
                report["audio_probes"] = {}
                candidates = [member for member in playable if not member.flag_bits & 1
                              and member.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)]
                if fast_zip is not None:
                    for member, result in fast_zip.probe_members(candidates, max_requests):
                        report["audio_probes"][member.filename] = result
                else:
                    directory_spans = source.chunks.copy()
                    incomplete_audio_probes = False
                    for member in candidates:
                        # A large first track cannot consume the allowance for the rest of the album.
                        source.budget = source.fetched + PREFIX_BYTES + 128 * 1024
                        source.max_requests = source.requests + max_requests
                        try:
                            data = read_member(archive, member, PREFIX_BYTES)
                            suffix = PurePosixPath(member.filename).suffix.lower()
                            result = (zip_worker.audio_prefix(data, suffix, member.file_size) if zip_worker is not None
                                      else compatibility_audio_prefix(data, suffix, member.file_size))
                        except (HTTPError, ArchiveRangeResponseError, NativeCatalogWorkerUnavailable):
                            raise
                        except (ValueError, RuntimeError, OSError, zipfile.BadZipFile) as exc:
                            result = {"prefix_status": str(exc)}
                        finally:
                            source.chunks = directory_spans.copy()
                        report["audio_probes"][member.filename] = result
                        if result and result.get("prefix_status") == "native audio prefix helper unavailable; retry indexing":
                            incomplete_audio_probes = True
                report["header_probe_pass_complete"] = (fast_zip is not None or not incomplete_audio_probes)
            for _, _, member in ([] if directory_only else selected_notes):
                result = {"path": member.filename}
                if max(member.file_size, member.compress_size) > 16384 or member.flag_bits & 1:
                    result["status"] = "skipped_size_or_encryption"
                else:
                    try:
                        result["metadata"] = sidecar_values(member.filename, read_member(archive, member, 16385))
                        candidate = normalize(result["metadata"].get("catalog_id", ""))
                        if re.fullmatch(CATNO, candidate, re.I):
                            add_catalog_candidate(catalog_evidence, candidate, "sidecar", member.filename)
                    except (ValueError, RuntimeError, OSError, zipfile.BadZipFile) as exc:
                        result["status"] = str(exc)
                report["sidecars"].append(result)
            if sample_flac:
                member = next((item for item in playable if item.filename.lower().endswith(".flac")
                               and not item.flag_bits & 1), None)
                if member:
                    try:
                        with archive.open(member) as stream:
                            report["audio_sample"] = {"path": member.filename, **sample_flac_member(stream)}
                    except (ValueError, RuntimeError, OSError, zipfile.BadZipFile) as exc:
                        report["audio_sample"] = {"path": member.filename, "status": str(exc)}
            if cover_sink is not None:
                if header_probes:
                    source.budget = source.fetched + 128 * 1024
                    source.max_requests = source.requests + max_requests
                cover = cache_zip_front_cover(archive, entries, source, cover_sink, limit=96 * 1024)
                if cover is not None:
                    report["cached_front_cover"] = cover
    report["catalog_id_candidates"] = sorted(catalog_evidence)
    report["catalog_id_candidate_evidence"] = catalog_evidence
    report["archive_http"] = {"requests": source.requests, "body_bytes": source.fetched,
                              "validator": "etag" if source.etag else "last-modified" if source.modified else None,
                              "etag": source.etag, "last_modified": source.modified}
    if fast_zip is not None or native_mediafire is not None:
        report["archive_http"]["transport"] = "winhttp"
    if fast_zip is not None and getattr(fast_zip, "provider", None) == "mega":
        report["archive_http"]["mega_mac"] = "not_verified_for_range_probes"
    if native_zip is not None:
        report["archive_http"]["directory_reader"] = "native"
    if fast_zip is not None:
        report["archive_http"]["member_reader"] = "native-batch"
    return report


@standalone_only
def optional(row, key, work):
    try:
        row[key] = work()
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as exc:
        row.setdefault("unavailable", {})[key] = str(exc)


@standalone_only
def cached_listing(http, cache, url, page, limit, refresh=False):
    listing = cache.page(url) if cache is not None and not refresh else None
    hit = listing is not None
    if listing is None:
        _, body, _ = http.get(url)
        listing = parse_listing(body, 20 if cache is not None else limit, include_forms=cache is not None)
        if cache is not None:
            cache.put_page(url, listing)
    output = {"page": page, "source": url, **listing}
    output["releases"] = [
        {key: value for key, value in row.items() if key not in {"download_form", "download_forms"}}
        for row in listing["releases"][:limit]
    ]
    if cache is not None:
        output["cache_hit"] = hit
    return output


@standalone_only
def export_records(path, cache):
    path = Path(path)
    stream = (gzip.open(path, "rt", encoding="utf-8-sig") if path.suffix.lower() == ".gz"
              else path.open(encoding="utf-8-sig"))
    with stream as file:
        for number, line in enumerate(file, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("expected a JSON object")
                identifier = item.get("id")
                if type(identifier) is not int or not 0 <= identifier < 2**63:
                    raise ValueError("id must be a nonnegative integer")
                album = item.get("album")
                if not isinstance(album, str) or not album.strip():
                    raise ValueError("album must be a nonempty string")
                prior = cache.listing(identifier)
                row = prior or release_row(identifier, album, None, [], None)
                evidence = row.setdefault("evidence", {})
                for key in ("album", "artist", "album_artist", "publisher", "catalog_id", "release_date",
                            "listed_at", "updated_at"):
                    value = item.get(key)
                    if value is not None:
                        if not isinstance(value, str):
                            raise ValueError(f"{key} must be a string or null")
                        row[key] = value
                        evidence[key] = "catalog export"
                tags = item.get("tags")
                if tags is not None:
                    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                        raise ValueError("tags must be an array of strings")
                    row["tags"] = tags
                    row["events"] = [value for tag in tags if (value := event(tag))]
                technical = item.get("technical")
                if technical is not None:
                    if not isinstance(technical, dict):
                        raise ValueError("technical must be an object")
                    row.setdefault("technical", {}).update(technical)
                urls = item.get("share_urls", [])
                if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
                    raise ValueError("share_urls must be an array of HTTP(S) URLs")
                offers = []
                for url in urls:
                    destination_key(url)
                    offers.append((describe_offer(url), url))
                tracklist = None
                tracks = item.get("tracks")
                if tracks is not None:
                    source = item.get("track_source_url")
                    if not isinstance(source, str):
                        raise ValueError("tracks require track_source_url")
                    destination_key(source)
                    if not isinstance(tracks, list):
                        raise ValueError("tracks must be an array")
                    entries = []
                    for track in tracks:
                        if not isinstance(track, dict) or not isinstance(track.get("title"), str) or not track["title"].strip():
                            raise ValueError("each track needs a title")
                        entries.append({key: track[key] for key in ("title", "artist", "number") if key in track})
                    tracklist = (source, entries)
                yield row, offers, tracklist
            except ValueError as exc:
                raise ValueError(f"{path}, line {number}: {exc}") from exc


@standalone_only
def saved_page_records(path, cache):
    path = Path(path)
    paths = sorted(path.glob("*.html")) if path.is_dir() else [path]
    if not paths:
        raise ValueError(f"{path}: no .html files found")
    for file in paths:
        try:
            page = parse_listing(file.read_bytes(), 20, include_forms=True)
            for parsed in page["releases"]:
                row = cache.listing(parsed["id"]) or parsed
                row.update({key: parsed[key] for key in ("album", "tags", "events", "download_forms")})
                advertised = parsed["technical"]["advertised_format"]
                if advertised:
                    row.setdefault("technical", {})["advertised_format"] = advertised
                if parsed["artist"]:
                    row["artist"] = parsed["artist"]
                row.setdefault("evidence", {}).update({
                    key: value for key, value in parsed["evidence"].items() if parsed[key] is not None})
                yield row, [], None
        except (ValueError, OSError) as exc:
            raise ValueError(f"{file}: {exc}") from exc


@standalone_only
def run_catalog_jobs(http, cache, args):
    if args.command == "status":
        if args.depth:
            cache.db.execute("BEGIN")
            return {**cache.summary(), "depth": cache.depth_report(), "storage": cache.storage_report()}
        return cache.summary()
    if args.command == "backup":
        cache.backup(args.path)
        return {"snapshot": args.path, "cache_summary": cache.summary()}
    if args.command == "cached":
        releases = cache.local_search(args.query, args.limit)
        for row in releases:
            release_catalog_candidates(row)
        return {"query": args.query, "releases": releases}
    if args.command == "songs":
        return {"query": args.query, "songs": cache.song_search(args.query, args.limit)}
    if args.command == "import-catalog":
        records = saved_page_records(args.path, cache) if args.html else export_records(args.path, cache)
        counts = cache.import_listings(records, baseline=args.baseline)
        return {**counts, "cache_summary": cache.summary()}
    if args.command == "add-copy":
        destination_key(args.url)
        listing = cache.listing(args.id)
        if listing is None:
            _, body, _ = http.get(ds_url(p="page", type=1, id=args.id))
            listing, forms = parse_detail(body, args.id)
            listing["download_forms"] = forms
        offer = describe_offer(args.url)
        if args.format_hint:
            offer["format_hint"] = args.format_hint
        copy_id = cache.add_copy(listing, offer, args.url)
        return {"id": args.id, "copy_id": copy_id, "copies": cache.copies_for(args.id)}
    if args.command == "census":
        results = []
        for job in cache.offer_jobs(args.limit, args.ids, args.refresh):
            row = json.loads(job["listing"])
            result = {"id": row["id"], "album": row["album"]}
            try:
                forms = row.get("download_forms") or ([row["download_form"]] if row.get("download_form") else [])
                details = args.details or not forms
                if details:
                    _, body, _ = http.get(row["url"])
                    _, forms = parse_detail(body, row["id"])
                offers, errors = resolve_offers(http, row, forms)
                ids = cache.put_offers(row["id"], offers, next(iter(errors.values()), None),
                                       {offer_source(fields) for fields in forms} if details else None)
                if offers:
                    result["offer"] = offers[0][1]
                result["copies"] = [{"source": source, "copy_id": copy_id, "offer": offer}
                                    for (source, offer, _), copy_id in zip(offers, ids)]
                if errors:
                    result["errors"] = errors
            except (ValueError, OSError) as exc:
                cache.put_offer(row["id"], None, None, str(exc))
                result["error"] = str(exc)
            results.append(result)
        return {"attempted": len(results), "results": results, "cache_summary": cache.summary()}
    results = []
    for job in cache.index_jobs(args.limit, args.host):
        result = {"id": job["release_id"], "copy_id": job["id"], "host": job["host"]}
        try:
            archive = inspect_zip(http, job["share_url"], args.byte_budget, directory_only=True)
            cache.put_archive(job["id"], archive, None)
            result.update({"format": archive["format"], "members": archive.get("members"),
                           "unknown_suffixes": archive.get("unknown_suffixes"), "archive_http": archive["archive_http"]})
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            cache.put_archive(job["id"], None, str(exc))
            result["error"] = str(exc)
        results.append(result)
    return {"attempted": len(results), "results": results, "cache_summary": cache.summary()}


@standalone_only
def main():
    from catalog_cache import restore_snapshot
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", metavar="SQLITE", help="SQLite file for pages and indexing queues")
    commands = parser.add_subparsers(dest="command", required=True)
    for mode in ("front", "search"):
        sub = commands.add_parser(mode)
        sub.add_argument("--limit", type=int, default=10, choices=range(1, 21), metavar="1..20")
        sub.add_argument("--page", type=int, default=1, metavar="N")
        sub.add_argument("--refresh", action="store_true", help="refresh the listing page in the local cache")
        if mode == "search":
            sub.add_argument("query")
            sub.add_argument("--by", choices=["blanket", "artist", "title", "tags", "credit"], default="blanket")
    for mode in ("release", "zip"):
        sub = commands.add_parser(mode)
        sub.add_argument("--byte-budget", type=int, default=262144)
        probe = sub.add_mutually_exclusive_group()
        probe.add_argument("--sample-flac", action="store_true")
        probe.add_argument("--directory-only", action="store_true")
        if mode == "release":
            sub.add_argument("id", type=int)
            sub.add_argument("--resolve", action="store_true", help="submit the release's download-link form")
            sub.add_argument("--inspect", action="store_true", help="resolve and inspect a supported archive")
            sub.add_argument("--offer-slot", type=int, help="which offer to inspect, 0 for primary or 1 for Mirror #1")
            sub.add_argument("--dojin-info", metavar="CD_URL")
            sub.add_argument("--creator", metavar="RELEASE_URL", help="Ultramarine Soundz release page")
            sub.add_argument("--musicbrainz", action="store_true")
            sub.add_argument("--catno", help="catalog number to use for a MusicBrainz search")
        else:
            sub.add_argument("url")
    sub = commands.add_parser("status", help="show cache coverage and resolved host counts")
    sub.add_argument("--depth", action="store_true", help="inspect coverage and SQLite storage without modifying the cache")
    sub = commands.add_parser("import-catalog", help="import local catalog data without making HTTP requests")
    sub.add_argument("path")
    sub.add_argument("--html", action="store_true", help="import a saved music listing page or a directory of .html files")
    sub.add_argument("--baseline", action="store_true", help="seed historical listings without recording activity")
    sub = commands.add_parser("backup", help="write a consistent SQLite snapshot to a new file")
    sub.add_argument("path")
    sub = commands.add_parser("restore", help="restore a snapshot into a new SQLite cache file")
    sub.add_argument("path")
    sub = commands.add_parser("cached", help="search cached releases and member filenames without HTTP")
    sub.add_argument("query", nargs="?", default="")
    sub.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="1..100")
    sub = commands.add_parser("songs", help="search locally sourced tracklists and audio filename hints")
    sub.add_argument("query")
    sub.add_argument("--limit", type=int, default=20, choices=range(1, 101), metavar="1..100")
    sub = commands.add_parser("census", help="resolve a batch of offers from cached listings")
    sub.add_argument("--limit", type=int, default=10, choices=range(1, 51), metavar="1..50")
    sub.add_argument("--ids", type=int, nargs="+", help="restrict the batch to selected cached release IDs")
    sub.add_argument("--details", action="store_true", help="visit release pages to discover mirror forms")
    sub.add_argument("--refresh", action="store_true", help="include listings checked within their cache TTL")
    sub = commands.add_parser("add-copy", help="attach a known host share URL to a release")
    sub.add_argument("id", type=int)
    sub.add_argument("url")
    sub.add_argument("--format-hint", choices=["zip", "rar", "7z", "gz"])
    sub = commands.add_parser("index", help="inspect a batch of cached archive links")
    sub.add_argument("--limit", type=int, default=3, choices=range(1, 21), metavar="1..20")
    sub.add_argument("--host", help="restrict the batch to this host, e.g. mediafire.com")
    sub.add_argument("--byte-budget", type=int, default=262144)
    args = parser.parse_args()
    if getattr(args, "page", 1) < 1:
        parser.error("--page starts at 1")
    if not 8192 <= getattr(args, "byte_budget", 262144) <= 2_097_152:
        parser.error("--byte-budget must be 8192..2097152")
    http = Http()
    cache = None
    try:
        job_commands = {"status", "cached", "songs", "census", "index", "add-copy", "import-catalog", "backup"}
        if args.command == "restore":
            path = args.cache or "doujin.sqlite"
            restore_snapshot(args.path, path)
            cache = CatalogCache(path)
            output = {"restored_to": path, "cache_summary": cache.summary()}
        elif args.command in job_commands:
            path = args.cache or "doujin.sqlite"
            if args.command == "backup" and not Path(path).is_file():
                raise ValueError(f"Catalog cache does not exist: {path}")
            cache = CatalogCache(path, read_only=args.command == "status" and args.depth)
            output = run_catalog_jobs(http, cache, args)
        elif args.command in ("front", "search"):
            if args.cache:
                cache = CatalogCache(args.cache)
            url = ds_url(p="home", page=args.page - 1) if args.command == "front" else ds_url(
                p="search", source=1, type=args.by, result=args.query, page=args.page - 1)
            output = cached_listing(http, cache, url, args.page, args.limit, args.refresh)
        elif args.command == "zip":
            output = inspect_zip(http, args.url, args.byte_budget, args.sample_flac, args.directory_only)
        else:
            _, body, _ = http.get(ds_url(p="page", type=1, id=args.id))
            output, forms = parse_detail(body, args.id)
            if args.cache:
                cache = CatalogCache(args.cache)
                cache.put_listing({**output, "download_forms": forms})
            if args.resolve or args.inspect:
                offers, errors = resolve_offers(http, output, forms)
                ids = cache.put_offers(args.id, offers, next(iter(errors.values()), None),
                                       {offer_source(fields) for fields in forms}) if cache is not None else [None] * len(offers)
                output["offers"] = [{"source": source, "offer": offer} for source, offer, _ in offers]
                if errors:
                    output.setdefault("unavailable", {})["offers"] = errors
                target = None
                if args.offer_slot is not None:
                    desired = f"doujinstyle:{args.offer_slot}" if args.offer_slot else "doujinstyle"
                    target = next((i for i, (source, _, _) in enumerate(offers) if source == desired), None)
                elif offers:
                    target = 0
                if target is not None:
                    _, offer, url = offers[target]
                    output["offer"] = offer
                    if args.inspect and can_inspect(url, offer):
                        optional(output, "archive", lambda: inspect_zip(
                            http, url, args.byte_budget, args.sample_flac, args.directory_only))
                        if cache is not None:
                            cache.put_archive(ids[target], output.get("archive"), output.get("unavailable", {}).get("archive"))
                    elif args.inspect:
                        output.setdefault("unavailable", {})["archive"] = "No direct archive adapter for this host"
                elif args.inspect:
                    output.setdefault("unavailable", {})["archive"] = "The selected offer did not resolve"
            if args.dojin_info:
                optional(output, "dojin_info", lambda: dojin_info(http, args.dojin_info, output))
            if args.creator:
                optional(output, "creator", lambda: creator_release(http, args.creator, output))
                creator = output.get("creator", {})
                if creator.get("title_matches") and creator.get("circle_matches"):
                    for field in ("catalog_id", "release_date"):
                        if creator[field] and (not output[field] or output[field] == creator[field]):
                            output[field] = creator[field]
                            output["evidence"][field] = creator["url"]
                    if cache is not None and creator["tracks"]:
                        with cache.write_transaction():
                            cache.put_tracklist(args.id, creator["url"], creator["tracks"])
            release_catalog_candidates(output)
            if args.musicbrainz:
                optional(output, "musicbrainz_candidates", lambda: musicbrainz(
                    http, output, args.catno or output["catalog_id"] or output.get("catalog_id_candidate")))
    except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        output = {"error": str(exc)}
    finally:
        if cache is not None:
            cache.close()
    output["http"] = http.log
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return int("error" in output)


if __name__ == "__main__":
    sys.exit(main())
