"""Source-only reference audio prefix parser, superseded by the native catalog helper.

Kept for differential/parity tests, not embedded in the foobar2000 worker.
"""
import struct
from native.worker_scope import standalone_only


PREFIX_BYTES = 4096


def probe_flac(data, flac_info, comment_tags):
    metadata = flac_info(data[:42])
    if metadata is None:
        return None
    result = {"metadata": {**metadata, "encoding": "lossless"}}
    pos = 42
    last = data[4] & 128
    while not last and pos + 4 <= len(data):
        block, last = data[pos] & 127, data[pos] & 128
        size = int.from_bytes(data[pos + 1:pos + 4], "big")
        pos += 4
        payload = data[pos:pos + size]
        if block == 6:
            picture = {"source": "FLAC PICTURE", "block_bytes": size}
            if len(payload) >= 8:
                mime_bytes = int.from_bytes(payload[4:8], "big")
                if 8 + mime_bytes <= len(payload):
                    picture["mime"] = payload[8:8 + mime_bytes].decode("ascii", "replace")
            result.setdefault("artwork", []).append(picture)
        if block == 3:
            result["seek_table_entries"] = size // 18
        if len(payload) != size:
            result["prefix_status"] = "metadata block extends beyond probe"
            break
        if block == 4:
            try:
                tags = comment_tags(payload)
            except ValueError as exc:
                result["prefix_status"] = str(exc)
                break
            if tags.pop("METADATA_BLOCK_PICTURE", None) is not None:
                result.setdefault("artwork", []).append({"source": "Vorbis comment"})
            result["tags"] = tags
        pos += size
    if not last and "prefix_status" not in result:
        result["prefix_status"] = "metadata continues beyond probe"
    return result


def probe_wave(data, file_size):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos = 12
    fields = None
    sample_rate = block_align = 0
    while pos + 8 <= len(data):
        kind = data[pos:pos + 4]
        size, = struct.unpack_from("<I", data, pos + 4)
        pos += 8
        if kind == b"fmt " and size >= 16 and len(data) >= pos + 16:
            code, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", data, pos)
            if code == 0xFFFE and size >= 40 and len(data) >= pos + 40:
                valid_bits, = struct.unpack_from("<H", data, pos + 18)
                guid = data[pos + 24:pos + 40]
                if guid[2:] == b"\0\0\0\0\x10\0\x80\0\0\xaa\0\x38\x9b\x71":
                    code = int.from_bytes(guid[:2], "little")
                    bits = valid_bits or bits
            if channels and rate and code in (1, 3):
                sample_rate, block_align = rate, align
                fields = {"codec": "PCM" if code == 1 else "IEEE float PCM", "encoding": "lossless",
                          "channels": channels, "sample_rate_hz": rate, "bits_per_sample": bits}
                if byte_rate == rate * align and align:
                    fields["nominal_bitrate_bps"] = byte_rate * 8
        if kind == b"data" and fields is not None:
            if block_align and size <= file_size - pos and size % block_align == 0:
                fields["duration_seconds"] = size / block_align / sample_rate
            break
        pos += size + (size & 1)
    return {"metadata": fields} if fields is not None else None


def probe_ogg(data, comment_tags):
    packet = bytearray()
    first_packet = True
    result = None
    offset = 0
    serial = data[14:18]
    while offset + 27 <= len(data) and data[offset:offset + 5] == b"OggS\0":
        page_start = offset
        count = data[offset + 26]
        offset += 27 + count
        sizes = data[page_start + 27:offset]
        if len(sizes) != count or offset + sum(sizes) > len(data):
            break
        if data[page_start + 14:page_start + 18] != serial:
            offset += sum(sizes)
            continue
        for size in sizes:
            packet.extend(data[offset:offset + size])
            offset += size
            if size == 255:
                continue
            if first_packet:
                first_packet = False
                if packet.startswith(b"OpusHead") and len(packet) >= 19 and packet[9]:
                    result = {"metadata": {"codec": "Opus", "encoding": "lossy",
                                           "channels": packet[9], "sample_rate_hz": 48000}}
                    original, = struct.unpack_from("<I", packet, 12)
                    if original:
                        result["metadata"]["input_sample_rate_hz"] = original
                elif packet.startswith(b"\x01vorbis") and len(packet) >= 30 and packet[11]:
                    rate, _, nominal, _ = struct.unpack_from("<Iiii", packet, 12)
                    if rate:
                        result = {"metadata": {"codec": "Vorbis", "encoding": "lossy",
                                               "channels": packet[11], "sample_rate_hz": rate}}
                        if nominal > 0:
                            result["metadata"]["nominal_bitrate_bps"] = nominal
                if result is None:
                    return None
            elif packet.startswith(b"OpusTags") or packet.startswith(b"\x03vorbis"):
                try:
                    tags = comment_tags(packet[8 if packet.startswith(b"OpusTags") else 7:])
                except ValueError as exc:
                    result["prefix_status"] = str(exc)
                    return result
                if tags.pop("METADATA_BLOCK_PICTURE", None) is not None:
                    result["artwork"] = [{"source": "Vorbis comment"}]
                result["tags"] = tags
                return result
            packet.clear()
    return result


def mp3_frame(data, pos):
    if len(data) < pos + 4:
        return None
    word, = struct.unpack_from(">I", data, pos)
    if word & 0xFFE00000 != 0xFFE00000 or (word >> 17) & 3 != 1:
        return None
    version, rate_id, bitrate_id = (word >> 19) & 3, (word >> 10) & 3, (word >> 12) & 15
    if version == 1 or rate_id == 3 or bitrate_id in (0, 15):
        return None
    rates = (44100, 48000, 32000)
    mpeg1 = version == 3
    rate = rates[rate_id] // (1 if mpeg1 else 2 if version == 2 else 4)
    bitrates = ((32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320) if mpeg1
                else (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160))
    bitrate = bitrates[bitrate_id - 1] * 1000
    frame_bytes = (144 if mpeg1 else 72) * bitrate // rate + ((word >> 9) & 1)
    channels = 1 if (word >> 6) & 3 == 3 else 2
    crc_bytes = 0 if word & (1 << 16) else 2
    return version, rate, channels, bitrate, frame_bytes, crc_bytes


def synchsafe(data):
    if len(data) == 4 and all(value < 128 for value in data):
        return sum(value << shift for value, shift in zip(data, (21, 14, 7, 0)))
    return None


def id3_frames(data, end):
    version, flags = data[3:5]
    if version not in (3, 4) or flags & 0xC0:
        return {}
    mapped = {"TIT2": "TITLE", "TPE1": "ARTIST", "TPE2": "ALBUMARTIST", "TALB": "ALBUM",
              "TRCK": "TRACKNUMBER", "TPOS": "DISCNUMBER", "TCON": "GENRE"}
    encodings = ("latin-1", "utf-16", "utf-16-be", "utf-8")
    fields = {}
    offset = 10
    while offset + 10 <= min(len(data), end):
        frame = data[offset:offset + 4].decode("ascii", "replace")
        if frame == "\0" * 4:
            break
        count = data[offset + 4:offset + 8]
        size = synchsafe(count) if version == 4 else int.from_bytes(count, "big")
        flags = data[offset + 9]
        offset += 10
        if not size or offset + size > end:
            break
        if frame == "APIC":
            fields.setdefault("artwork", []).append({"source": "ID3 APIC", "block_bytes": size})
        if offset + size > len(data):
            break
        encoding = data[offset]
        if frame in mapped and not flags and encoding < len(encodings):
            try:
                value = data[offset + 1:offset + size].decode(encodings[encoding]).strip("\0")
            except UnicodeError:
                value = ""
            if value:
                fields.setdefault("tags", {})[mapped[frame]] = value.split("\0")
        offset += size
    return fields


def probe_mp3(data):
    pos = 0
    result = {}
    if data.startswith(b"ID3") and len(data) >= 10 and (size := synchsafe(data[6:10])) is not None:
        pos = 10 + size
        result = id3_frames(data, pos)
        if data[3] == 4 and data[5] & 16:
            pos += 10
        result["id3v2_bytes"] = pos
    frame = mp3_frame(data, pos)
    if frame is None:
        if result:
            result["prefix_status"] = "audio frame unavailable in probe"
        return result or None
    version, rate, channels, bitrate, frame_bytes, crc_bytes = frame
    next_frame = mp3_frame(data, pos + frame_bytes)
    if next_frame is None or next_frame[:2] != frame[:2]:
        return result or None
    metadata = {"codec": "MP3", "encoding": "lossy", "sample_rate_hz": rate,
                "channels": channels, "first_frame_bitrate_bps": bitrate}
    xing = pos + 4 + crc_bytes + (17 if channels == 1 else 32) if version == 3 else (
        pos + 4 + crc_bytes + (9 if channels == 1 else 17))
    if data[xing:xing + 4] in (b"Xing", b"Info") and xing + 12 <= len(data):
        flags, frames = struct.unpack_from(">II", data, xing + 4)
        if flags & 1 and frames:
            metadata["duration_seconds"] = frames * (1152 if version == 3 else 576) / rate
    return {**result, "metadata": metadata}


@standalone_only
def audio_prefix(data, suffix, file_size, flac_info, comment_tags):
    if data.startswith(b"fLaC"):
        result = probe_flac(data, flac_info, comment_tags)
    elif data.startswith(b"RIFF"):
        result = probe_wave(data, file_size)
    elif data.startswith(b"OggS"):
        result = probe_ogg(data, comment_tags)
    elif suffix == ".mp3":
        result = probe_mp3(data)
    else:
        result = None
    if result is None:
        return {"prefix_status": "no supported audio header in probe"}
    metadata = result.get("metadata", {})
    if duration := metadata.get("duration_seconds"):
        metadata["file_average_bitrate_bps"] = round(file_size * 8 / duration)
    return result
