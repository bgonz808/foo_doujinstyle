"""Source-only Python oracle for the installed C++ ZIP inventory projection.

Never embed this module in the foobar2000 worker. Its purpose is differential
parity checking before removing the remaining Python archive inspector.
"""
from collections import Counter
from pathlib import PurePosixPath
import re

from doujin import archive_sidecar, member_entry, MEMBER_TYPES


def project(members):
    entries = [member for member in members if not member.is_dir()]
    roots = set()
    for member in members:
        parts = PurePosixPath(member.filename.replace('\\', '/')).parts
        if parts and (member.is_dir() or len(parts) > 1):
            roots.add(parts[0])
    note_types = {'.nfo': 0, '.cue': 1, '.txt': 2}
    playable, notes = [], []
    for index, member in enumerate(members):
        if member.is_dir() or archive_sidecar(member.filename):
            continue
        path = PurePosixPath(member.filename.replace('\\', '/'))
        suffix = path.suffix.lower()
        if suffix in MEMBER_TYPES['audio_candidate']:
            playable.append(index)
        elif suffix in note_types and (suffix != '.txt' or re.search(
                r'readme|info|release|tracklist|credit', path.stem, re.I)):
            notes.append((note_types[suffix], len(path.parts), member.filename, index))
    inventory = [member_entry(member) for member in entries]
    return {
        'members': len(entries), 'audio_members': len(playable),
        'audio': [{'path': members[i].filename, 'compressed_bytes': members[i].compress_size,
                   'uncompressed_bytes': members[i].file_size,
                   'zip_method': members[i].compress_type,
                   'encrypted': bool(members[i].flag_bits & 1)} for i in playable[:100]],
        'inventory': inventory,
        'member_types': dict(Counter(item['kind'] for item in inventory)),
        'unknown_suffixes': dict(Counter(item['suffix'] or '(none)'
                                         for item in inventory if item['kind'] == 'unknown')),
        'sidecar_candidates': [item[3] for item in sorted(notes)[:3]],
        'artwork': [item for item in inventory if item['kind'] == 'artwork'],
        'roots': sorted(roots),
        'entry_indices': [i for i, member in enumerate(members) if not member.is_dir()],
        'playable_indices': playable,
    }
