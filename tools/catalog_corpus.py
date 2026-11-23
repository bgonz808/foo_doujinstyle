"""Read-only archive/audio/provider coverage audit of a user-provided SQLite catalog.

No HTTP requests or SQLite writes. Never include cached signed links, filenames,
titles, tokens, offer bodies, or embedded metadata in the output report.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import sqlite3

REQUIRED = frozenset({"releases", "copies", "release_copies", "pages"})
KNOWN_TARGETS = (19732, 28590)


def inspect(source: Path) -> dict:
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("Catalog snapshot must be an ordinary file")
    digest = sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    # This is for a stable CLOSED snapshot only. Live WAL catalogs must be
    # copied using SQLite backup first, not opened with immutable=1.
    with sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1") as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Catalog SQLite integrity check failed")
        tables = {x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED.issubset(tables):
            raise ValueError("Missing expected catalog tables")
        schema_version = db.execute("PRAGMA user_version").fetchone()[0]
        labels, hosts, archive_formats, methods, suffixes = (Counter() for _ in range(5))
        archive_sizes = Counter()
        archive_count = 0
        audio_count = 0
        release_count = 0
        for (payload,) in db.execute("SELECT listing FROM releases"):
            row = json.loads(payload)
            if not isinstance(row, dict):
                raise ValueError("Malformed release listing")
            label = str((row.get("technical") or {}).get("advertised_format") or "unspecified").upper()
            labels[label] += 1
            release_count += 1
        for host, payload in db.execute("SELECT host, archive FROM copies"):
            hosts[str(host or "unknown")] += 1
            if payload is None:
                continue
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("Malformed archive inventory")
            archive_formats[str(data.get("format") or "unknown").lower()] += 1
            archive_count += 1
            size = data.get("archive_bytes")
            if type(size) is int and size >= 0:
                archive_sizes["at_least_2_gib" if size >= 2 * 1024 ** 3 else "under_2_gib"] += 1
            for member in data.get("audio") or []:
                if not isinstance(member, dict) or not isinstance(member.get("path"), str):
                    raise ValueError("Malformed audio inventory")
                suffixes[PurePosixPath(member["path"]).suffix.lower() or "(none)"] += 1
                methods[str(member.get("zip_method", "unknown"))] += 1
                audio_count += 1
        targets = {
            str(release_id): bool(db.execute("SELECT 1 FROM releases WHERE id=?", (release_id,)).fetchone())
            for release_id in KNOWN_TARGETS
        }
    missing_technical = [
        {"audio_suffix": key, "inventory_count": suffixes.get(key, 0)}
        for key in (".wav", ".ogg", ".opus", ".tta", ".mp3", ".flac")
    ]
    return {
        "schema_version": 1, "kind": "catalog-coverage-audit",
        "source_sha256": digest.hexdigest(),
        "source_size_bytes": source.stat().st_size,
        "sqlite_user_version": schema_version,
        "catalog_releases": release_count,
        "resolved_copies": sum(hosts.values()),
        "inspected_archives": archive_count,
        "audio_inventory_members": audio_count,
        "advertised_formats_unverified": dict(sorted(labels.items())),
        "resolved_hosts_only": dict(sorted(hosts.items())),
        "inspected_archive_formats": dict(sorted(archive_formats.items())),
        "audio_member_suffixes_only": dict(sorted(suffixes.items())),
        "audio_zip_methods": dict(sorted(methods.items())),
        "archive_sizes": dict(sorted(archive_sizes.items())),
        "reported_release_present": targets,
        "repro_fixture_gaps": missing_technical,
        "limitations": [
            "Website advertised_format is not decoded audio format.",
            "Only copies with an archived member inventory count as inspected.",
            "The snapshot contains no original archive or audio bytes: hashes/PCM correctness cannot be inferred.",
            "This report intentionally excludes raw URLs, titles, filenames, offer JSON and provider secrets.",
            "Treat a WAL-enabled live database as mutable; export a closed SQLite backup before inspection.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Closed, immutable SQLite snapshot")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refuse to overwrite existing corpus evidence")
    report = inspect(args.db)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write("\n")
    print(f"Catalog releases: {report['catalog_releases']}, "
          f"copies: {report['resolved_copies']}, "
          f"inventoried audio: {report['audio_inventory_members']}")
    print(f"Local report: {args.output}")


if __name__ == "__main__":
    main()
