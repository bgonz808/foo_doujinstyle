"""Inspect frame boundaries without decompressing Zstandard payloads.

This is a structural probe, not a substitute for authenticated input or decoder
validation. The official seek table is checked against observed frame offsets.
"""
import struct

FRAME_MAGIC = 0xFD2FB528
SEEK_MAGIC = 0x184D2A5E
FOOTER_MAGIC = 0x8F92EAB1


def need(ok, message):
    if not ok:
        raise ValueError(message)


def read_exact(f, n):
    b = f.read(n)
    need(len(b) == n, "Truncated Zstd structure")
    return b


def integer(f, n):
    return int.from_bytes(read_exact(f, n), "little")


def probe(path):
    length = path.stat().st_size
    frames, skips = [], []
    seek_table = None
    with path.open("rb") as f:
        while f.tell() < length:
            start = f.tell()
            magic = integer(f, 4)
            if 0x184D2A50 <= magic <= 0x184D2A5F:
                size = integer(f, 4)
                need(size <= length - f.tell(), "Truncated skippable frame")
                if magic == SEEK_MAGIC and size >= 9 and f.tell() + size == length:
                    need(size <= 16 * 1024 * 1024, "Seek table exceeds probe bound")
                    data = read_exact(f, size)
                    count, descriptor, footer = struct.unpack("<IBI", data[-9:])
                    if footer == FOOTER_MAGIC:
                        need(not (descriptor & 0x7F), "Reserved seek descriptor bits")
                        width = 12 if descriptor & 0x80 else 8
                        need(size == count * width + 9 and count == len(frames), "Seek table size/count")
                        for i, frame in enumerate(frames):
                            c, u = struct.unpack_from("<II", data, i * width)
                            need(c == frame["compressed_bytes"] and
                                 (frame["content_bytes"] is None or u == frame["content_bytes"]),
                                 "Seek entry mismatch")
                        seek_table = {"entries": count, "checksums": bool(descriptor & 0x80),
                                      "bytes": size + 8}
                else:
                    f.seek(size, 1)
                skips.append({"offset": start, "magic": hex(magic), "bytes": size + 8})
                continue
            need(magic == FRAME_MAGIC, "Unexpected frame magic")
            descriptor = integer(f, 1)
            need(not (descriptor & 0x08), "Reserved frame descriptor bit")
            single = bool(descriptor & 0x20)
            checksum = bool(descriptor & 4)
            if not single:
                integer(f, 1)  # window descriptor
            dict_size = (0, 1, 2, 4)[descriptor & 3]
            dictionary = integer(f, dict_size) if dict_size else 0
            flag = descriptor >> 6
            content_size = (1 if single else 0, 2, 4, 8)[flag]
            content = integer(f, content_size) if content_size else None
            if flag == 1:
                content += 256
            blocks = 0
            while True:
                header = integer(f, 3)
                block_type = (header >> 1) & 3
                size = header >> 3
                need(block_type != 3 and size <= 128 * 1024, "Invalid block header")
                stored = 1 if block_type == 1 else size
                need(stored <= length - f.tell(), "Truncated block")
                f.seek(stored, 1)
                blocks += 1
                if header & 1:
                    break
            if checksum:
                integer(f, 4)
            frames.append({"offset": start, "compressed_bytes": f.tell() - start,
                           "content_bytes": content, "dictionary_id": dictionary,
                           "checksum": checksum, "blocks": blocks})
            need(len(frames) <= 1000000, "Frame count bound")
    need(bool(frames), "No Zstd frames")
    return {"archive_bytes": length, "frame_count": len(frames), "frames": frames,
            "skippable_frames": skips, "seek_table": seek_table}
