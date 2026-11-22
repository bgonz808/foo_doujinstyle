"""Source-only Python MEGA reference; installed worker uses native CNG/WinHTTP.

Retired from the installed zipapp at v0.0.57. Keep for differential tests.
"""
import base64
from contextlib import contextmanager
import ctypes
from email.message import Message
from hmac import compare_digest
import json
import os
import re
from secrets import randbelow
from struct import pack
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from native.worker_scope import standalone_only


@standalone_only
def xor_bytes(left, right):
    return (int.from_bytes(left, "big") ^ int.from_bytes(right, "big")).to_bytes(len(left), "big")


@standalone_only
class WindowsAes:
    """Use Windows CNG for the AES primitive without an installed client or wheel."""

    def __init__(self, key):
        dll = ctypes.WinDLL("bcrypt.dll")
        ptr, num = ctypes.c_void_p, ctypes.c_uint32

        def bind(name, *arguments):
            fn = getattr(dll, "BCrypt" + name)
            fn.argtypes = arguments
            fn.restype = ctypes.c_long

            def invoke(*args):
                if fn(*args) < 0:
                    raise ValueError("Windows AES operation failed")
            return invoke

        self.open = bind("OpenAlgorithmProvider", ctypes.POINTER(ptr), ctypes.c_wchar_p, ctypes.c_wchar_p, num)
        self.set = bind("SetProperty", ptr, ctypes.c_wchar_p, ptr, num, num)
        self.generate = bind("GenerateSymmetricKey", ptr, ctypes.POINTER(ptr), ptr, num, ptr, num, num)
        self.destroy = bind("DestroyKey", ptr)
        self.close = bind("CloseAlgorithmProvider", ptr, num)
        self.encrypt = bind("Encrypt", ptr, ptr, num, ptr, ptr, num, ptr, num, ctypes.POINTER(num), num)
        self.decrypt = bind("Decrypt", ptr, ptr, num, ptr, ptr, num, ptr, num, ctypes.POINTER(num), num)
        self.key = key

    def crypt(self, data, *, iv=None, decrypt=False):
        if not data:
            return b""
        algorithm, handle = ctypes.c_void_p(), ctypes.c_void_p()
        self.open(ctypes.byref(algorithm), "AES", None, 0)
        try:
            mode = ctypes.create_unicode_buffer("ChainingModeCBC" if iv is not None else "ChainingModeECB")
            self.set(algorithm, "ChainingMode", mode, ctypes.sizeof(mode), 0)
            key = ctypes.create_string_buffer(self.key)
            self.generate(algorithm, ctypes.byref(handle), None, 0, key, len(self.key), 0)
            try:
                source = ctypes.create_string_buffer(data, len(data))
                dest = ctypes.create_string_buffer(len(data))
                initial = ctypes.create_string_buffer(iv, len(iv)) if iv is not None else None
                written = ctypes.c_uint32()
                operation = self.decrypt if decrypt else self.encrypt
                operation(handle, source, len(data), None, initial, len(iv or b""),
                          dest, len(dest), ctypes.byref(written), 0)
                if written.value != len(data):
                    raise ValueError("Windows AES returned an unexpected length")
                return dest.raw
            finally:
                self.destroy(handle)
        finally:
            self.close(algorithm, 0)


@standalone_only
class PortableAes:
    """AES backend for running the same worker outside Windows."""

    def __init__(self, key):
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            raise ValueError("Direct MEGA downloads on this platform require the cryptography Python package") from None
        self.cipher, self.algorithm, self.modes = Cipher, algorithms.AES(key), modes

    def crypt(self, data, *, iv=None, decrypt=False):
        mode = self.modes.ECB() if iv is None else self.modes.CBC(iv)
        cipher = self.cipher(self.algorithm, mode)
        context = cipher.decryptor() if decrypt else cipher.encryptor()
        return context.update(data) + context.finalize()


@standalone_only
def link_parts(url):
    target = urlsplit(url)
    hosts = {"mega.nz", "www.mega.nz", "mega.co.nz", "www.mega.co.nz"}
    match = re.fullmatch(r"/file/([A-Za-z0-9_-]{8})/?", target.path)
    legacy = re.fullmatch(r"!([A-Za-z0-9_-]{8})!([A-Za-z0-9_-]{43})", target.fragment) if target.path == "/" else None
    parsed = (match[1], target.fragment) if match else legacy.groups() if legacy else None
    if (target.scheme != "https" or target.hostname not in hosts or target.username is not None
            or target.port not in (None, 443) or target.query or not parsed
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", parsed[1])):
        raise ValueError("MEGA archive downloads require a public file link with its key")
    return parsed[0], decode_base64(parsed[1])


@standalone_only
def is_public_file(url):
    try:
        link_parts(url)
        return True
    except ValueError:
        return False


@standalone_only
def decode_base64(value):
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


@standalone_only
def storage_url(url):
    target = urlsplit(url)
    if (target.scheme != "https" or target.username is not None or target.port not in (None, 443)
            or target.fragment or not (target.hostname or "").endswith(
                (".userstorage.mega.co.nz", ".userstorage.mega.nz", ".userstorage.mega.io"))):
        raise ValueError("MEGA returned an unrecognized HTTPS storage endpoint")
    return url


@standalone_only
class MegaRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, target):
        if request.get_method() == "POST":
            return None
        return super().redirect_request(request, fp, code, message, headers, storage_url(target))


@standalone_only
def public_file(opener, handle):
    query = f"https://g.api.mega.co.nz/cs?id={randbelow(2**32)}"
    body = json.dumps([{"a": "g", "p": handle, "g": 1, "ssl": 2}]).encode("ascii")
    with opener.open(Request(query, body, {"Content-Type": "application/json"}), timeout=20) as response:
        data = response.read(65537)
    try:
        if len(data) > 65536:
            raise ValueError
        results = json.loads(data)
    except (UnicodeError, ValueError):
        raise ValueError("MEGA returned an invalid public-file API response") from None
    record = results[0] if isinstance(results, list) and len(results) == 1 else results
    code = record.get("e") if isinstance(record, dict) else record
    if type(code) is int and code < 0:
        message = {-9: "public file is unavailable", -16: "public file is blocked",
                   -17: "download quota reached", -18: "public file is temporarily unavailable"}
        raise ValueError("MEGA " + message.get(code, f"API error {code}"))
    if (not isinstance(record, dict) or not isinstance(record.get("g"), str)
            or type(record.get("s")) is not int or record["s"] <= 0 or not isinstance(record.get("at"), str)):
        raise ValueError("MEGA returned incomplete public-file metadata")
    return storage_url(record["g"]), record["s"], record["at"]


@standalone_only
def file_name(cipher, attributes):
    decoded = cipher.crypt(decode_base64(attributes), iv=bytes(16), decrypt=True)
    if not decoded.startswith(b"MEGA{"):
        raise ValueError("MEGA could not decrypt the file name with the public-link key")
    try:
        document = json.loads(decoded[4:].rstrip(b"\0"))
        if isinstance(document, dict) and isinstance(document.get("n"), str) and document["n"]:
            return document["n"]
    except (ValueError, UnicodeError):
        pass
    raise ValueError("MEGA returned invalid file name attributes")


@standalone_only
def decrypt_span(cipher, nonce, position, encrypted):
    offsets = range(position // 16, (position + len(encrypted) + 15) // 16)
    blocks = b"".join(pack(">8sQ", nonce, offset) for offset in offsets)
    start = position % 16
    return xor_bytes(encrypted, cipher.crypt(blocks)[start:start + len(encrypted)])


@contextmanager
@standalone_only
def mega_errors():
    try:
        yield
    except HTTPError as exc:
        if exc.code in (429, 509):
            raise ValueError(f"MEGA download rate or quota limit (HTTP {exc.code})") from None
        raise ValueError(f"MEGA download returned HTTP {exc.code}") from None
    except URLError:
        raise ValueError("Could not connect to the MEGA download service") from None


@standalone_only
class MegaRanges:
    """Adapt MEGA storage offset URLs to the archive reader's range interface."""

    def __init__(self, url):
        handle, packed = link_parts(url)
        self.opener = build_opener(MegaRedirect())
        self.cipher = (WindowsAes if os.name == "nt" else PortableAes)(xor_bytes(packed[:16], packed[16:]))
        self.nonce = packed[16:24]
        with mega_errors():
            self.endpoint, self.size, attributes = public_file(self.opener, handle)
        self.filename = file_name(self.cipher, attributes)

    def get(self, url, *, headers, cap, policy=""):
        span = re.fullmatch(r"bytes=(\d*)-(\d+)", headers["Range"])
        first, last = (int(span[1]), min(self.size - 1, int(span[2]))) if span[1] else (
            max(0, self.size - int(span[2])), self.size - 1)
        length = last - first + 1
        if not 0 < length <= cap:
            raise ValueError("MEGA range is outside the archive or probe budget")
        parts = urlsplit(self.endpoint)
        target = urlunsplit(parts._replace(path=f"{parts.path.rstrip('/')}/{first}-{last}"))
        with mega_errors(), self.opener.open(Request(target, headers={"Accept-Encoding": "identity"}), timeout=25) as result:
            if result.status not in (200, 206) or result.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("MEGA storage did not return the requested ciphertext range")
            if result.status == 206 and result.headers.get("Content-Range") != f"bytes {first}-{last}/{self.size}":
                raise ValueError("MEGA storage returned a mismatched Content-Range")
            if result.headers.get("Content-Length", str(length)) != str(length):
                raise ValueError("MEGA storage returned an unexpected range length")
            raw = result.read(length + 1)
        if len(raw) != length:
            raise ValueError("MEGA storage returned an incomplete ciphertext range")
        response = Message()
        response["Content-Range"] = f"bytes {first}-{last}/{self.size}"
        response.add_header("Content-Disposition", "attachment", filename=("utf-8", "", self.filename))
        return response, decrypt_span(self.cipher, self.nonce, first, raw), url


@standalone_only
def verified_chunks(response, size, cipher, nonce, mac):
    position, file_mac = 0, bytes(16)
    chunk_number = 1
    while position < size:
        amount = min(chunk_number * 131072, 1048576, size - position)
        encrypted = response.read(amount)
        if len(encrypted) != amount:
            raise ValueError("MEGA archive ended before the advertised length")
        plaintext = decrypt_span(cipher, nonce, position, encrypted)
        padded = plaintext + bytes(-amount % 16)
        chunk_mac = cipher.crypt(padded, iv=nonce * 2)[-16:]
        file_mac = cipher.crypt(xor_bytes(file_mac, chunk_mac))
        yield plaintext
        chunk_number += 1
        position += amount
    condensed = xor_bytes(file_mac[:4], file_mac[4:8]) + xor_bytes(file_mac[8:12], file_mac[12:])
    if not compare_digest(mac, condensed):
        raise ValueError("MEGA file verification failed")
    if response.read(1):
        raise ValueError("MEGA archive exceeds its advertised length")


@contextmanager
@standalone_only
def open_public_file(url):
    handle, packed = link_parts(url)
    cipher = (WindowsAes if os.name == "nt" else PortableAes)(xor_bytes(packed[:16], packed[16:]))
    opener = build_opener(MegaRedirect())
    with mega_errors():
        target, size, attributes = public_file(opener, handle)
        name = file_name(cipher, attributes)
        with opener.open(Request(target, headers={"Accept-Encoding": "identity"}), timeout=25) as response:
            if response.status != 200 or response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("MEGA did not return the full ciphertext")
            if response.headers.get("Content-Length", str(size)) != str(size):
                raise ValueError("MEGA storage length disagrees with the public file size")
            yield name, verified_chunks(response, size, cipher, packed[16:24], packed[24:])
