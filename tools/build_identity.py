"""Development-only deterministic Git/Git-free component identity generator.

No network, no wall clock, no workflow run IDs. The generated header is ignored
and is built from *exact* source bytes; built binaries contain only concise
identity constants, not a copy of the entire manifest.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

if __package__:
    from .version_policy import read_policy
else:
    from version_policy import read_policy

EXCLUDED_DIRS = frozenset({'.git', '.venv', 'venv', '__pycache__', '.vs', '.vscode', '.idea',
                           '.pytest_cache', '.mypy_cache', '.ruff_cache', 'dist', 'out',
                           '.toolchains', 'local-fixtures', 'captured-fixtures', 'xwin-splat',
                           'foobar2000-SDK'})
EXCLUDED_SUFFIXES = frozenset({'.pyc', '.pyo', '.obj', '.o', '.dll', '.exe', '.pdb', '.ilk',
                               '.plist', '.fb2k-component', '.log', '.trace', '.pcap', '.pcapng', '.har'})


def git(root, *args):
    try:
        proc = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True)
        return proc.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def ignored(path):
    return (any(part in EXCLUDED_DIRS or part.startswith('build-') or part == 'build'
                for part in path.parts[:-1]) or
            path.name in {'SOURCE-ATTRIBUTION.json'} or path.name.startswith('.env') or
            path.suffix in EXCLUDED_SUFFIXES or path.name.endswith('~'))


def source_paths(root, git_available):
    if git_available:
        files = git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z')
        if files is None:
            raise RuntimeError('Cannot enumerate Git files')
        names = {Path(os.fsdecode(value)) for value in files.split(b'\0') if value}
    else:
        names = {p.relative_to(root) for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    return sorted((p for p in names if not ignored(p)), key=lambda p: p.as_posix().encode('utf-8'))


def source_digest(root, git_available):
    """SHA-256 over explicitly length-prefixed, path-sorted file records.

    Paths use UTF-8 and slash separators; no timestamps, absolute paths, OS
    metadata or machine identity. The .gitattributes policy enforces LF bytes.
    Deletion is represented by a distinct record type. Symlink targets are data.
    """
    digest = sha256(b'DS-SOURCE-V1\0')
    casefold = set()
    count = 0
    for relative in source_paths(root, git_available):
        name = relative.as_posix().encode('utf-8')
        if relative.as_posix().casefold() in casefold:
            raise ValueError(f'Case-colliding source path: {relative}')
        casefold.add(relative.as_posix().casefold())
        full = root / relative
        if full.is_symlink():
            kind, data = 2, os.fsencode(os.readlink(full))
        elif full.is_file():
            kind, data = 1, full.read_bytes()
        else:
            kind, data = 3, b''  # deleted tracked file
        digest.update(bytes([kind]))
        digest.update(len(name).to_bytes(4, 'big'))
        digest.update(name)
        digest.update(len(data).to_bytes(8, 'big'))
        digest.update(sha256(data).digest())
        count += 1
    return digest.hexdigest(), count


def identify(root):
    root = Path(root).resolve()
    policy = read_policy(root)
    head = git(root, 'rev-parse', '--verify', 'HEAD')
    top = git(root, 'rev-parse', '--show-toplevel')
    available = bool(head and top and Path(os.fsdecode(top)).resolve() == root)
    if (root / '.git').exists() and not available:
        raise RuntimeError("Git identity unavailable in Git checkout")
    tree_sha, count = source_digest(root, available)
    sha, dirty = "", False
    if available:
        sha = head.decode('ascii')
        status = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
        if status is None:
            raise RuntimeError("Cannot determine Git checkout cleanliness")
        dirty = bool(status)
    return dict(base_version=policy.base, revision=policy.revision,
                release_stage=policy.stage, product_version=policy.product_version,
                version=policy.diagnostic_version(commit=sha, source_sha256=tree_sha, dirty=dirty),
                git_commit=sha, dirty=dirty, source_sha256=tree_sha,
                source_file_count=count, source_kind='git' if available else 'snapshot')


def header(identity):
    fields = {'DOUJIN_BASE_VERSION': identity['base_version'],
              'DOUJIN_FULL_VERSION': identity['version'],
              'DOUJIN_PRODUCT_VERSION': identity['product_version'],
              'DOUJIN_PROJECT_REVISION': str(identity['revision']),
              'DOUJIN_RELEASE_STAGE': identity['release_stage'],
              'DOUJIN_GIT_COMMIT': identity['git_commit'],
              'DOUJIN_SOURCE_SHA256': identity['source_sha256'],
              'DOUJIN_SOURCE_KIND': identity['source_kind']}
    return ('// Generated by tools/build_identity.py; never edit or commit.\n#pragma once\n'
            + ''.join(f'#define {key} {json.dumps(value)}\n' for key, value in fields.items())
            + f"#define DOUJIN_DIRTY_BUILD {1 if identity['dirty'] else 0}\n")


def write_if_changed(path, data):
    """Keep identical generated headers stable for incremental compilation."""
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument('--header', type=Path)
    ap.add_argument('--json', type=Path)
    args = ap.parse_args()
    identity = identify(args.root)
    if args.header:
        write_if_changed(args.header, header(identity).encode('utf-8'))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(identity, sort_keys=True))


if __name__ == '__main__':
    main()
