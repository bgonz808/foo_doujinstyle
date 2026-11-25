"""Send catalog operations to the bundled native worker."""
from email.message import Message
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from tempfile import NamedTemporaryFile
from threading import Lock
from urllib.error import HTTPError
from zipfile import ZipFile
from native.worker_scope import standalone_only


# Bounds apply to one JSONL frame, not the entire transfer. Large archive
# inventory replies remain supported, but a corrupt or compromised child
# cannot force an unbounded readline allocation.
MAX_NATIVE_FRAME_BYTES = 64 * 1024 * 1024


class NativeCatalogWorkerUnavailable(OSError):
    """The native process could not start or stopped responding."""


class NativeCatalogWorkerStartupFailed(NativeCatalogWorkerUnavailable):
    """The worker failed before this request was sent."""


class ReferencePublisher:
    def __init__(self, executable=None, *, bundle=None, host_zlib=None):
        self.executable = executable
        self.bundle = bundle
        self.host_zlib = host_zlib
        self.process = None
        self._request_lock = Lock()

    def start(self):
        if self.bundle is not None:
            with ZipFile(self.bundle) as source:
                contents = source.read("publish_references.exe")
            digest = sha256(contents).hexdigest()
            self.executable = Path(self.bundle).parent / f"publisher-{digest[:24]}.exe"
            if not self.executable.is_file() or sha256(self.executable.read_bytes()).hexdigest() != digest:
                temporary = None
                try:
                    with NamedTemporaryFile(dir=self.executable.parent, suffix=".tmp", delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write(contents)
                    temporary.chmod(0o700)
                    try:
                        temporary.replace(self.executable)
                    except PermissionError:
                        # Another worker may have installed and opened the same image.
                        if not self.executable.is_file() or sha256(self.executable.read_bytes()).hexdigest() != digest:
                            raise
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        self.process = subprocess.Popen([str(self.executable), "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        **({"stderr":subprocess.DEVNULL} if os.environ.get("DOUJIN_LIVE_PROVIDER_SMOKE")=="1" else {}),
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if self.process.stdout.readline(128).rstrip(b"\r\n") != b"DoujinStyle catalog 1":
            raise self.interrupted("startup")

    def request(self, database, operation, *, on_event=None, **fields):
        # A second request on the same JSONL pipe during an event callback
        # would deadlock; an unrelated concurrent request corrupts framing.
        if not self._request_lock.acquire(blocking=False):
            raise NativeCatalogWorkerUnavailable("Concurrent or reentrant native IPC request")
        try:
            return self._request(database, operation, on_event=on_event, **fields)
        finally:
            self._request_lock.release()

    def _request(self, database, operation, *, on_event=None, **fields):
        if self.process is None:
            try:
                self.start()
            except NativeCatalogWorkerUnavailable as error:
                raise NativeCatalogWorkerStartupFailed(str(error)) from error
            except OSError as error:
                raise NativeCatalogWorkerStartupFailed(
                    f"Could not start native catalog worker ({operation}): {error}") from error
        if "stream_events" in fields:
            raise ValueError("stream_events is reserved for the native event protocol")
        request = {"db": str(database), "operation": operation, **fields}
        if on_event is not None:
            request["stream_events"] = True
        try:
            self.process.stdin.write(json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n")
            self.process.stdin.flush()
            while True:
                line = self.process.stdout.readline(MAX_NATIVE_FRAME_BYTES + 1)
                if len(line) > MAX_NATIVE_FRAME_BYTES:
                    raise OSError("Native catalog worker frame exceeds byte limit")
                if not line.endswith(b"\n"):
                    raise self.interrupted(operation)
                try:
                    response = json.loads(line)
                except (ValueError, UnicodeError) as error:
                    raise OSError("Native catalog worker returned invalid JSON") from error
                if not isinstance(response, dict):
                    raise OSError("Native catalog worker returned a non-object frame")
                if "event" not in response:
                    break
                if on_event is None or set(response) != {"event"} or not isinstance(response["event"], dict):
                    raise OSError("Unsolicited or malformed native catalog event")
                on_event(response["event"])
            if ("result" in response) == ("error" in response):
                raise OSError("Native catalog worker returned ambiguous terminal frame")
            if "error" in response and not isinstance(response["error"], str):
                raise OSError("Native catalog worker returned a malformed error")
        except BrokenPipeError as error:
            raise self.interrupted(operation) from error
        except Exception:
            # A callback exception or protocol failure leaves the stream
            # unsynchronized. Never reuse or replay this native process.
            self.close(force=True)
            raise
        # A well-formed remote application error is a valid terminal frame;
        # the synchronized worker remains reusable for the next request.
        if "error" in response:
            raise OSError("Native catalog worker: " + response["error"])
        return response["result"]

    def interrupted(self, operation):
        try:
            code = self.process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            code = None
        if code is None:
            detail = "output pipe closed"
        elif os.name == "nt":
            detail = f"exit status 0x{code & 0xffffffff:08X}"
        else:
            detail = f"exit status {code}"
        self.close(force=True)
        return NativeCatalogWorkerUnavailable(f"Native catalog worker did not reply ({operation}, {detail})")

    @standalone_only
    def publish_release(self, database, output, root_key, release_id, copy_id):
        return self.request(database, "publish_release", id=release_id, copy_id=copy_id,
                            output=str(output.resolve()), root=root_key or "")

    @standalone_only
    def publish(self, database, output, root_key, references):
        if not references:
            return []
        result = self.request(database, "publish", output=str(output.resolve()), root=root_key or "",
                              references=references)
        if len(result) != len(references):
            raise OSError("Native catalog worker returned an incomplete publication result")
        return [tuple(item) for item in result]

    def transport_peer(self):
        return ReferencePublisher(self.executable, host_zlib=self.host_zlib)

    @standalone_only
    def mediafire_resolve(self, url, referrer):
        response = self.request(None, "mediafire_resolve", url=url, referrer=referrer or "")
        if response["status"] != 200:
            status = response["status"]
            raise HTTPError(url, status, f"MediaFire share page returned HTTP {status}", None, None)
        return response["url"], response["referrer"], response["policy"]

    @standalone_only
    def mediafire_range(self, url, start, length, referrer, policy, validator):
        response = self.request(None, "mediafire_range", url=url, start=start, length=length,
                                referrer=referrer or "", policy=policy, validator=validator or "")
        if response["status"] != 206:
            raise ValueError(f"MediaFire archive range returned HTTP {response['status']}; response body skipped")
        headers = Message()
        for key, value in response["headers"].items():
            if value:
                headers[key] = value
        return headers, bytes.fromhex(response["body"])

    @standalone_only
    def mediafire_zip_open(self, url, referrer, policy, budget, requests, suffix):
        if self.host_zlib is None:
            return None
        return self.request(None, "mediafire_zip_open", host_zlib=str(self.host_zlib), url=url,
                            referrer=referrer or "", policy=policy, budget=budget, requests=requests, suffix=suffix)

    @standalone_only
    def mega_zip_open(self, url, budget, requests, suffix):
        if self.host_zlib is None:
            return None
        return self.request(None, "mega_zip_open", host_zlib=str(self.host_zlib), url=url,
                            budget=budget, requests=requests, suffix=suffix)

    @standalone_only
    def mediafire_download_archive(self, url, referrer, filename, output, release_id):
        return self.request(None, "mediafire_download_archive", url=url, referrer=referrer or "",
                            filename=filename or "", output=str(output.resolve()), id=release_id)

    @standalone_only
    def mega_download_archive(self, url, output, release_id):
        return self.request(None, "mega_download_archive", url=url, output=str(output.resolve()), id=release_id)

    @standalone_only
    def audio_prefix(self, data, suffix, file_size):
        return self.request(None, "audio_prefix", prefix=data.hex(), suffix=suffix, file_size=file_size)

    @standalone_only
    def zip_inventory(self, members):
        # One native projection for MediaFire, MEGA, and local ZIP directory rows.
        # Indexed records are bounded by the native JSON frame and ZIP parser limits.
        rows = []
        for member in members:
            rows.append({"filename": member.filename, "compress_size": member.compress_size,
                         "file_size": member.file_size, "CRC": member.CRC,
                         "header_offset": member.header_offset, "compress_type": member.compress_type,
                         "flag_bits": member.flag_bits,
                         "raw_name": getattr(member, "raw_name", b"").hex(),
                         "encoding": getattr(member, "encoding", ""),
                         "name_warning": getattr(member, "name_warning", False),
                         "alternate_name": getattr(member, "alternate_name", "") or ""})
        return self.request(None, "zip_inventory", members=rows)

    @standalone_only
    def zip_catalog_layout(self, size, tail):
        return self.request(None, "zip_catalog_layout", size=size, tail=tail.hex())

    @standalone_only
    def zip_catalog_members(self, start, entries, directory):
        return self.request(None, "zip_catalog_members", start=start, entries=entries, directory=directory.hex())

    @standalone_only
    def zip_catalog_payload(self, member, boundary, local):
        return self.request(None, "zip_catalog_payload", local=local.hex(), name=member.raw_name.hex(),
                            offset=member.header_offset, boundary=boundary, packed=member.compress_size,
                            size=member.file_size, crc=member.CRC, method=member.compress_type, flags=member.flag_bits)

    def close(self, *, force=False):
        if self.process is None:
            return
        process, self.process = self.process, None
        if force:
            # A bad event or callback must not leave native work committing
            # for another two seconds while we wait for a graceful EOF.
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        process.stdout.close()
        try:
            process.wait(timeout=2 if not force else 0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
