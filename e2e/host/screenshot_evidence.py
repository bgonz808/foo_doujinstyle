"""Inventory real Robot/FlaUI screenshots from an isolated Windows guest.

This is evidence collection, not proof that the depicted UI is correct.
Never upload images automatically: the user must inspect/redact before sharing.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import struct

EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp"})
MAX_IMAGES = 80
MAX_IMAGE_BYTES = 25 * 1024 * 1024


def valid_header(path: Path) -> bool:
    if path.stat().st_size < 64 or path.stat().st_size > MAX_IMAGE_BYTES:
        return False
    with path.open("rb") as stream:
        head = stream.read(64)
        if path.suffix.lower() == ".png":
            if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
                return False
            width, height = struct.unpack(">II", head[16:24])
            return 1 <= width <= 16384 and 1 <= height <= 16384
        if path.suffix.lower() in (".jpg", ".jpeg"):
            if head[:2] != b"\xff\xd8":
                return False
            stream.seek(-2, 2)
            return stream.read(2) == b"\xff\xd9"
        if path.suffix.lower() == ".bmp":
            if head[:2] != b"BM" or len(head) < 26:
                return False
            width, height = struct.unpack("<ii", head[18:26])
            return 1 <= width <= 16384 and 1 <= abs(height) <= 16384
    return False


def collect(folder: Path) -> dict:
    """Only local files below Robot's output folder; no symlinks or URLs."""
    if not folder.is_dir():
        return {"status": "missing", "count": 0, "files": [], "reason": "robot_folder_missing"}
    found = sorted(p for p in folder.rglob("*") if p.suffix.lower() in EXTENSIONS)
    if not found:
        return {"status": "missing", "count": 0, "files": [], "reason": "no_screenshots"}
    if len(found) > MAX_IMAGES:
        return {"status": "invalid", "count": 0, "files": [], "reason": "too_many_images"}
    values = []
    for path in found:
        if path.is_symlink() or not path.is_file():
            return {"status": "invalid", "count": 0, "files": [], "reason": "unsafe_image_path"}
        if not valid_header(path):
            return {"status": "invalid", "count": 0, "files": [], "reason": "bad_image_header"}
        contents = path.read_bytes()
        values.append({"path": path.relative_to(folder).as_posix(), "sha256": sha256(contents).hexdigest(),
                       "size_bytes": len(contents)})
    return {"status": "present", "count": len(values), "files": values,
            "reason": "file_headers_and_hashes_only_not_visual_verification"}
