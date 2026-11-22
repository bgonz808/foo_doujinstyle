"""Package an fb2k component with deterministic ZIP ordering and metadata."""
import argparse
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED


def pack(destination, entries):
    ordered = sorted(entries, key=lambda e: e[0].encode('utf-8'))
    if len({name for name, _ in ordered}) != len(ordered):
        raise ValueError('Duplicate package member')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, source in ordered:
            if name.startswith('/') or '\\' in name or '..' in Path(name).parts:
                raise ValueError('Unsafe package member')
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, Path(source).read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)
    return destination


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('output', type=Path)
    ap.add_argument('entry', nargs='+', help='Archive/path=source-file pairs')
    args = ap.parse_args()
    entries = []
    for item in args.entry:
        name, separator, source = item.partition('=')
        if not separator or not name or not source:
            ap.error('Each entry must be destination=source-path')
        entries.append((name, source))
    print(pack(args.output, entries))


if __name__ == '__main__':
    main()
