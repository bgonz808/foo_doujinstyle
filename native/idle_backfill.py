"""Oldest-first metadata backfill using observed DoujinStyle navigation links."""
import ctypes
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from zipimport import zipimporter as _NativeBundleImporter
if isinstance(__loader__, _NativeBundleImporter):
    from native.runtime_contract import DS, HTML_ACCEPT, ds_url
else:
    from doujin import DS, HTML_ACCEPT, ds_url
from native.worker_scope import standalone_only, runtime_deprecated


IDLE_UA = "DoujinCatalogPrototype/0.1 (idle catalog backfill)"
PAGE_INTERVAL = 10


@runtime_deprecated('Windows input-idle timing remains in Python until the native job launcher replaces it')
def wait_for_idle(previous_start):
    if sys.platform != "win32":
        raise ValueError("The desktop idle scheduler requires Windows.")

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]

    user32 = ctypes.windll.user32
    kernel = ctypes.windll.kernel32
    sample = LastInput(ctypes.sizeof(LastInput), 0)
    while True:
        if not user32.GetLastInputInfo(ctypes.byref(sample)):
            raise OSError("Could not check Windows input idle time")
        idle_ms = (kernel.GetTickCount() - sample.dwTime) & 0xFFFFFFFF
        now = time.monotonic()
        if idle_ms >= 30_000 and now - previous_start >= PAGE_INTERVAL:
            return now
        time.sleep(1)


@standalone_only
def page_links(body, page_url, headers, *, publisher=None):
    if publisher is not None:
        try:
            result = publisher.request(None, "site_parse", kind="browse_links", html=body.hex(),
                                       url=page_url, referrer_policy=headers.get("Referrer-Policy", ""))
            return {int(offset): tuple(value) for offset, value in result.items()}
        except OSError:
            pass
    compatibility = globals().get("compatibility_page_links")
    if compatibility is None:
        raise OSError("Native browse parser unavailable; no Python production fallback")
    return compatibility(body, page_url, headers)


@standalone_only
def compatibility_page_links(body, page_url, headers):
    from urllib.parse import parse_qs, urljoin, urlsplit
    from lxml import html
    from doujin import class_token, document_policy, referrer_policy
    page = html.fromstring(body, parser=html.HTMLParser(encoding="UTF-8"))
    policy = document_policy(page, headers)
    links = {page_offset(page_url): (page_url, policy)}
    for link in page.xpath(f"//*[{class_token('pageScroll')}]//a[@href]"):
        target = urljoin(page_url, link.get("href"))
        parsed = urlsplit(target)
        if parsed.scheme != "https" or parsed.netloc != "doujinstyle.com" or parsed.path != "/":
            continue
        fields = parse_qs(parsed.query)
        offset = fields.get("page", [""])[0]
        if fields.get("p") == ["home"] and offset.isascii() and offset.isdecimal() and len(offset) <= 6:
            link_policy = ("no-referrer" if "noreferrer" in link.get("rel", "").lower().split() else
                           referrer_policy(link.get("referrerpolicy", "")) or policy)
            links[int(offset)] = (target, link_policy)
    return links


@standalone_only
def page_offset(url):
    from urllib.parse import parse_qs, urlsplit
    return int(parse_qs(urlsplit(url).query).get("page", ["0"])[0])


@runtime_deprecated('Robots advisory still uses Python pending C++ transport and policy parity')
def report_robots_advisory(http, home, emit, publisher=None):
    try:
        if publisher is not None:
            response = publisher.request(None, "site_resource", kind="robots",
                                         url="https://doujinstyle.com/robots.txt", cap=64 * 1024)
            if response["status"] in (404, 410):
                emit("robots.txt is unavailable. Continuing the requested idle backfill.")
                return
            if response["status"] != 200:
                emit("robots.txt was not available for an advisory. Continuing the requested idle backfill.")
                return
            headers, body = {}, bytes.fromhex(response["body"])
        else:
            from zipimport import zipimporter
            if isinstance(__loader__, zipimporter):
                raise OSError("Native robots.txt transport unavailable")
            headers, body, _ = http.get(urljoin(DS, "robots.txt"), cap=64 * 1024, redirects=False,
                                        headers={"User-Agent": IDLE_UA, "Accept": "text/plain"})
    except HTTPError as exc:
        if exc.code not in {404, 410}:
            raise
        emit("robots.txt is unavailable. Continuing the requested idle backfill.")
        return
    if headers.get("Location"):
        emit("robots.txt redirected. Continuing the requested idle backfill.")
        return
    rules = RobotFileParser()
    rules.parse(body.decode("utf-8-sig", errors="replace").splitlines())
    if not rules.can_fetch(IDLE_UA, home):
        emit("DoujinStyle's robots.txt excludes this crawler. Continuing the requested rate-limited backfill.")


@runtime_deprecated('Native checkpoint and browse commits complete; Python still owns Windows idle pacing and result delivery')
def idle_backfill(http, cache, emit, slot=wait_for_idle):
    # The installed Windows worker runs a bounded native page step. The
    # original Python checkpoint algorithm is source-only test reference.
    if not getattr(cache, "native", False) or sys.platform != "win32":
        reference = globals().get("compatibility_idle_backfill")
        if reference is None:
            raise OSError("Installed idle backfill requires the native worker")
        return reference(http, cache, emit, slot=slot)
    home = ds_url(p="home", page=0)
    report_robots_advisory(http, home, emit, cache.publisher if cache.native else None)
    emit("Idle backfill armed. Waiting for 30 seconds of Windows input idle.")
    started = 0
    state = None
    while True:
        started = slot(started)
        result = cache.publisher.request(cache.path, "catalog_browse_step", state=state)
        if "saved" in result:
            emit(f"Idle catalog page {result['saved'] + 1} • {result.get('rows', 0)} listings saved")
        if result["message"] and (result["done"] or "saved" not in result):
            emit(result["message"])
        if result["done"]:
            return
        state = result["state"]
        if not state:
            raise OSError("Native browse worker returned no continuation")


@standalone_only
def compatibility_idle_backfill(http, cache, emit, slot=wait_for_idle):
    home = ds_url(p="home", page=0)
    checkpoint = cache.backfill_checkpoint()
    if checkpoint and checkpoint["complete"]:
        emit("Idle backfill already reached the newest page.")
        return
    report_robots_advisory(http, home, emit)

    request_time = time.monotonic()

    def fetch(url, referring_page=None, policy=""):
        nonlocal request_time
        request_time = slot(request_time)
        request_headers = {"User-Agent": IDLE_UA, "Accept": HTML_ACCEPT}
        if referring_page:
            request_headers["Referer"] = referring_page
        headers, body, _ = http.get(url, headers=request_headers, redirects=False, policy=policy)
        if headers.get("Location"):
            raise ValueError("The DoujinStyle browse page redirected. Idle backfill stopped.")
        worker = getattr(cache, "publisher", None) if getattr(cache, "native", False) else None
        if worker is not None:
            # Both the foreground search and idle backfill consume the same
            # bounded C++ HTML parser, including its form and Unicode rules.
            page = worker.request(None, "site_parse", kind="listing", html=body.hex(), forms=True)
        else:
            from doujin import __dict__ as legacy
            parser = legacy.get("parse_listing")
            if parser is None:
                raise OSError("Native idle listing parser unavailable; no Python production fallback")
            page = parser(body, 20, include_forms=True)
        return page, page_links(body, url, headers, publisher=worker)

    emit("Idle backfill armed. Waiting for 30 seconds of Windows input idle.")
    head, links = fetch(home)
    tail = max(links)
    target, policy = links[tail]
    referrer = home
    if checkpoint:
        target, referrer = checkpoint["url"], checkpoint["referrer"]
        policy = checkpoint.get("referrer_policy", "")
        anchor_ids = set(checkpoint["ids"])
        for _ in range(8):
            page, links = fetch(target, referrer, policy)
            ids = [row["id"] for row in page["releases"]]
            overlap = [identifier for identifier in ids if identifier in anchor_ids]
            anchor_order = [identifier for identifier in checkpoint["ids"] if identifier in overlap]
            if len(overlap) >= min(3, len(anchor_ids)) and overlap == anchor_order:
                emit(f"Relocated checkpoint to page {page_offset(target) + 1}.")
                break
            if not ids or not anchor_ids or any(a <= b for a, b in zip(ids, ids[1:])):
                raise ValueError("Checkpoint could not be relocated in the observed page ordering.")
            direction = 1 if min(ids) > max(anchor_ids) else -1 if max(ids) < min(anchor_ids) else 0
            neighbor = links.get(page_offset(target) + direction) if direction else None
            if not neighbor:
                raise ValueError("Checkpoint overlap is too small to resume safely.")
            referrer = target
            target, policy = neighbor
        else:
            raise ValueError("Checkpoint moved beyond the eight-page relocation budget.")
    elif target == home:
        page = head
    else:
        page, links = fetch(target, referrer, policy)

    while True:
        offset = page_offset(target)
        next_page = links.get(offset - 1)
        if offset > 0 and not next_page:
            raise ValueError("The observed navigation did not include the next newer page.")
        cache.put_page(target, page, checkpoint={
            "url": target, "referrer": referrer, "ids": [row["id"] for row in page["releases"]],
            "referrer_policy": policy, "complete": offset == 0,
        })
        emit(f"Idle catalog page {offset + 1} • {page['rows_on_page']} listings saved")
        if offset == 0:
            emit("Idle backfill reached the newest page.")
            return
        referrer = target
        target, policy = next_page
        page, links = fetch(target, referrer, policy)
