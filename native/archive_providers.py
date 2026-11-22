"""Archive-provider capabilities available to the installed worker.

The standalone test/reference source may use Python MEGA cryptography.  The
embedded worker never imports it; native C++ validates the link again and owns
both range inspection and authenticated whole-file downloads.
"""
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit
from zipimport import zipimporter


def is_public_mega_file(url):
    try:
        target = urlsplit(url)
        if (target.scheme != "https" or target.hostname not in
                {"mega.nz", "www.mega.nz", "mega.co.nz", "www.mega.co.nz"}
                or target.username is not None or target.port not in (None, 443) or target.query):
            return False
        alphabet = r"[A-Za-z0-9_-]"
        modern = re.fullmatch(rf"/file/{alphabet}{{8}}/?", target.path)
        legacy = re.fullmatch(rf"!{alphabet}{{8}}!{alphabet}{{43}}", target.fragment) if target.path == "/" else None
        return bool((modern and re.fullmatch(rf"{alphabet}{{43}}", target.fragment)) or legacy)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class ArchiveProvider:
    can_read: Callable[[str], bool]
    open_ranges: Callable | None = None
    open_file: Callable | None = None


def mediafire_link(url):
    address = urlsplit(url)
    host = address.hostname or ""
    return address.scheme == "https" and (host == "mediafire.com" or host.endswith(".mediafire.com"))


# Source-only oracle is useful for offline parity tests.  A bundled zipapp
# must not import an external package with this name from the working folder.
if isinstance(__loader__, zipimporter):
    mega_provider = ArchiveProvider(is_public_mega_file)
else:
    from native.mega_download import MegaRanges, is_public_file, open_public_file
    mega_provider = ArchiveProvider(is_public_file, MegaRanges, open_public_file)

ARCHIVE_PROVIDERS = {
    "mediafire.com": ArchiveProvider(mediafire_link),
    "mega.nz": mega_provider,
}
