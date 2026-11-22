"""Bounded catalog ZIP access over an already-validated range source."""
from dataclasses import dataclass
from email.message import Message
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZIP_STORED
import zlib



_TAIL_BYTES = 22 + 65535 + 20 + 56


@dataclass(frozen=True)
class Member:
    filename: str
    raw_name: bytes
    compress_size: int
    file_size: int
    CRC: int
    header_offset: int
    compress_type: int
    flag_bits: int
    encoding: str
    name_warning: bool
    alternate_name: str | None

    def is_dir(self):
        return self.filename.endswith("/")

def decode_members(rows):
    # The native directory parser resolves archive-scoped encoding evidence in
    # one pass. Never re-decode untrusted member names inside production Python.
    for row in rows:
        if len(row) != 13:
            raise BadZipFile("Native ZIP parser omitted filename encoding evidence")
        raw, packed, size, crc, offset, method, flags, _, _, name, encoding, warning, alternate = row
        yield Member(name, bytes.fromhex(raw), packed, size, crc, offset, method, flags,
                     encoding, warning, alternate or None)


class MediaFireZip:
    """The native worker owns the HTTP snapshot and all compressed member bytes."""

    def __init__(self, worker, view, budget, max_requests, *, provider="mediafire"):
        if provider not in ("mediafire", "mega"):
            raise ValueError("Unsupported native archive provider")
        self.provider = provider
        self.worker = worker
        self.members = list(decode_members(view["members"]))
        self.size = view["size"]
        self.etag, self.modified = view["etag"] or None, view["modified"] or None
        headers = Message()
        if view["disposition"]:
            headers["Content-Disposition"] = view["disposition"]
        self.filename = view.get("filename") or headers.get_filename()
        self.budget, self.max_requests = budget, max_requests
        self.update_counts(view)

    def update_counts(self, view):
        self.fetched, self.requests = view["fetched"], view["requests"]

    def infolist(self):
        return self.members

    def probe_members(self, members, requests):
        for begin in range(0, len(members), 32):
            batch = members[begin:begin + 32]
            keys = [(member.header_offset, member.file_size, PurePosixPath(member.filename).suffix.lower())
                    for member in batch]
            view = self.worker.request(None, self.provider + "_zip_probes", members=keys, requests=requests)
            self.update_counts(view)
            yield from zip(batch, view["observations"], strict=True)

    def read_member(self, member, limit):
        view = self.worker.request(None, self.provider + "_zip_member", offset=member.header_offset, limit=limit,
                                   budget=self.budget - self.fetched, requests=self.max_requests - self.requests)
        self.update_counts(view)
        if "error" in view:
            raise BadZipFile(view["error"])
        return bytes.fromhex(view["data"])


class CatalogZip:
    """Delegate on-disk ZIP structure to C++; inflate only requested payload bytes."""

    def __init__(self, source, worker, members, directory_start):
        self.source = source
        self.worker = worker
        self.members = members
        offsets = sorted(entry.header_offset for entry in members)
        self.boundaries = dict(zip(offsets, offsets[1:] + [directory_start]))

    @classmethod
    def open(cls, source, worker, suffix_bytes):
        length = min(source.size, suffix_bytes)
        tail = source.read_at(source.size - length, length)
        layout = worker.zip_catalog_layout(source.size, tail)
        if layout is None and len(tail) < min(source.size, _TAIL_BYTES):
            # Keep the existing archive budget. If the compatibility reader can
            # work with fewer bytes than the maximum suffix, let it try instead.
            length = min(source.size, _TAIL_BYTES)
            if length - len(tail) <= source.budget - source.fetched and source.requests < source.max_requests:
                tail = source.read_at(source.size - length, length)
                layout = worker.zip_catalog_layout(source.size, tail)
        if layout is None:
            return None
        start, length, count = layout
        rows = worker.zip_catalog_members(start, count, source.read_at(start, length))
        if rows is None:
            return None

        members = list(decode_members(rows))
        if len({entry.header_offset for entry in members}) != len(members):
            return None
        return cls(source, worker, members, start)

    def infolist(self):
        return self.members

    def read_member(self, member, limit):
        payload = self.worker.zip_catalog_payload(member, self.boundaries[member.header_offset],
            self.source.read_at(member.header_offset, 30 + len(member.raw_name)))
        if payload is None:
            raise BadZipFile("ZIP local header failed native validation")
        want = min(limit, member.file_size)
        complete = want == member.file_size
        if member.compress_type == ZIP_STORED:
            data = self.source.read_at(payload, want)
        else:
            inflater = zlib.decompressobj(-zlib.MAX_WBITS)
            output = bytearray()
            remaining = member.compress_size
            try:
                while remaining and (complete or len(output) < want):
                    count = min(4096, remaining)
                    chunk = self.source.read_at(payload, count)
                    if len(chunk) != count:
                        raise BadZipFile("Truncated ZIP member")
                    payload += count
                    remaining -= count
                    output.extend(inflater.decompress(chunk, max(1, want - len(output) + int(complete))))
                    if len(output) > want:
                        raise BadZipFile("ZIP member exceeded its declared length")
                    if inflater.eof:
                        if remaining or inflater.unused_data:
                            raise BadZipFile("ZIP compressed size disagrees with member payload")
                        break
            except zlib.error as exc:
                raise BadZipFile("Invalid ZIP DEFLATE member") from exc
            if complete and not inflater.eof:
                raise BadZipFile("Truncated ZIP DEFLATE member")
            data = bytes(output)
        if len(data) != want or (complete and zlib.crc32(data) != member.CRC):
            raise BadZipFile("ZIP member length or CRC mismatch")
        return data


def read_member(archive, member, limit):
    if isinstance(archive, (CatalogZip, MediaFireZip)):
        return archive.read_member(member, limit)
    with archive.open(member) as stream:
        return stream.read(limit)
