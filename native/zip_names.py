"""Source-only Python reference for native archive-scoped ZIP filename resolution."""
from native.worker_scope import standalone_only


@standalone_only
def _text(data, encoding):
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        return None
    if text and all(ord(ch) >= 32 and ord(ch) != 127 for ch in text):
        return text
    return None


@standalone_only
def _japanese(text):
    return sum("\u3041" <= ch <= "\u30fa" or "\u4e00" <= ch <= "\u9fff" for ch in text or "")


@standalone_only
def resolve_names(records):
    candidates = []
    japanese_archive = False
    for raw, flags, extra, bad_extra in records:
        native = _text(raw, "utf-8") if flags & 0x800 else None
        unicode_path = _text(extra, "utf-8") if extra else None
        cp932 = _text(raw, "cp932") if not flags & 0x800 and not raw.isascii() else None
        count = _japanese(cp932)
        if cp932 and (unicode_path == cp932 or count >= 3 or
                      (count >= 2 and (any("\u3041" <= ch <= "\u30fa" for ch in cp932) or
                                       raw.count(b"\\") > cp932.count("\\")))):
            japanese_archive = True
        warning = bool(bad_extra or (extra and not unicode_path) or (native and unicode_path and native != unicode_path)
                       or (flags & 0x800 and not native))
        candidates.append((raw, native, unicode_path, cp932 if count else None, warning))

    for raw, native, unicode_path, cp932, warning in candidates:
        if native is not None:
            yield native, "utf-8", warning, unicode_path if unicode_path != native else None
        elif unicode_path is not None:
            yield unicode_path, "unicode-path", warning, None
        elif japanese_archive and cp932 is not None:
            yield cp932, "cp932-inferred", warning, None
        else:
            yield raw.decode("cp437"), "cp437", warning, None
