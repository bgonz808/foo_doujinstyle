"""Unpack the checksum-pinned SQLite amalgamation for native builds."""
from hashlib import sha256, sha3_256
from pathlib import Path
import sys
from zipfile import ZipFile


ROOT = "sqlite-amalgamation-3540100"
SHA3 = "92281c86e2957f9b9305890f9ac04204caa7b677cc868dc326d942689df28e30"
JSON_SHA256 = "aaf127c04cb31c406e5b04a63f1ae89369fccde6d8fa7cdda1ed4f32dfc5de63"


def prepare(output):
    archive = Path(__file__).with_name(ROOT + ".zip")
    if sha3_256(archive.read_bytes()).hexdigest() != SHA3:
        raise ValueError("SQLite 3.54.1 amalgamation checksum mismatch")
    header = Path(__file__).parent / "third_party" / "json.hpp"
    if sha256(header.read_bytes()).hexdigest() != JSON_SHA256:
        raise ValueError("nlohmann/json 3.12.0 header checksum mismatch")
    output.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive) as source:
        for filename in ("sqlite3.c", "sqlite3.h"):
            destination = output / filename
            data = source.read(f"{ROOT}/{filename}")
            if not destination.exists() or destination.read_bytes() != data:
                destination.write_bytes(data)
    schema = Path(__file__).with_name("catalog_schema.sql").read_text(encoding="utf-8")
    generated = '#pragma once\ninline constexpr char catalog_schema[] = R"schema(\n' + schema + ')schema";\n'
    destination = output / "catalog_schema.hpp"
    if not destination.exists() or destination.read_text(encoding="utf-8") != generated:
        destination.write_text(generated, encoding="utf-8")


if __name__ == "__main__":
    prepare(Path(sys.argv[1]))
