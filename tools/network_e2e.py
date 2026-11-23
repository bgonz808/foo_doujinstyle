"""Explicitly allowlisted real-network transport probes (not host playback).

Exact DNS -> globally routable IP check -> pinned TCP socket -> validated TLS
SNI/hostname. Redirects are re-authorized. No URL or response body is logged.
Offline by default; external hypervisor/firewall policy is still REQUIRED.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import sys
from urllib.parse import urljoin, urlsplit

SCHEMA=1
CASE_ID=re.compile(r"[a-z][a-z0-9_-]{2,63}\Z")
HOST=re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
REDIRECTS={301,302,303,307,308}

class NetworkPolicyError(ValueError):
    pass

def validate_policy(doc: dict) -> dict:
    if not isinstance(doc,dict) or doc.get("schema_version")!=SCHEMA:
        raise NetworkPolicyError("Invalid policy schema")
    if type(doc.get("enabled")) is not bool:
        raise NetworkPolicyError("Network policy must explicitly specify enabled")
    hosts=doc.get("hosts")
    if not isinstance(hosts,list) or len(hosts)>20 or len(hosts)!=len(set(hosts)) or any(
        not isinstance(h,str) or len(h)>253 or not HOST.fullmatch(h)
        or h.startswith("www.") and h.count(".")<2 or ".." in h or h.endswith(".")
        or "." not in h or h.endswith((".local",".test",".invalid",".internal"))
        or any(len(part)>63 or part.startswith("-") or part.endswith("-") for part in h.split("."))
        for h in hosts):
        raise NetworkPolicyError("Exact nonwildcard public DNS hostname list required")
    for h in hosts:
        try:
            ipaddress.ip_address(h)
        except ValueError:
            pass
        else:
            raise NetworkPolicyError("IP literals cannot be allowlisted")
    limits={"max_requests":(1,32),"max_total_bytes":(1,32*1024*1024),
            "max_response_bytes":(1,4*1024*1024),"timeout_seconds":(1,30),
            "max_redirects":(0,3)}
    for key,(lo,hi) in limits.items():
        n=doc.get(key)
        if type(n) is not int or not lo<=n<=hi:
            raise NetworkPolicyError("Invalid bounded policy field: "+key)
    if doc["max_response_bytes"]>doc["max_total_bytes"]:
        raise NetworkPolicyError("Response budget exceeds total budget")
    if doc["enabled"] and not hosts:
        raise NetworkPolicyError("Enabled network policy requires approved hosts")
    return doc

def inspect_url(url: str, policy: dict) -> tuple[str,str]:
    if not isinstance(url,str) or len(url)>4096:
        raise NetworkPolicyError("Unbounded or invalid URL")
    try:
        p=urlsplit(url)
        port=p.port
    except ValueError as exc:
        raise NetworkPolicyError("Invalid port or address") from exc
    host=p.hostname
    if (p.scheme!="https" or port not in (None,443) or p.username is not None
        or p.password is not None or p.fragment or not host
        or host not in policy["hosts"] or p.netloc.startswith("[")
        or not p.path.startswith("/") and p.path!=""):
        raise NetworkPolicyError("HTTPS exact allowlist-only endpoint required")
    # Explicitly reject literal IPv4/IPv6 and noncanonical host spellings.
    if p.netloc.split(":")[0]!=host:
        raise NetworkPolicyError("Canonical lower-case DNS hostnames only")
    return host,p.path or "/"

def pinned_address(host: str) -> tuple[int,tuple]:
    answers=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM,proto=socket.IPPROTO_TCP)
    if not answers:
        raise NetworkPolicyError("Missing DNS answers")
    permitted=[]
    for fam,kind,proto,canon,sockaddr in answers:
        if fam not in (socket.AF_INET,socket.AF_INET6):
            raise NetworkPolicyError("Unexpected address family")
        addr=ipaddress.ip_address(sockaddr[0])
        if not addr.is_global:
            raise NetworkPolicyError("DNS response included nonpublic IP")
        permitted.append((fam,sockaddr))
    return permitted[0]

class PinnedConnection(http.client.HTTPSConnection):
    def __init__(self,host: str,endpoint: tuple[int,tuple],timeout: int):
        super().__init__(host,port=443,timeout=timeout,context=ssl.create_default_context())
        self._endpoint=endpoint

    def connect(self):
        family,sockaddr=self._endpoint
        sock=socket.socket(family,socket.SOCK_STREAM,socket.IPPROTO_TCP)
        sock.settimeout(self.timeout)
        try:
            sock.connect(sockaddr)
            if sock.getpeername()[0]!=sockaddr[0]:
                raise NetworkPolicyError("TCP peer changed after DNS pin")
            # Keep TLS SNI and certificate verification bound to the allowlisted host.
            self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except BaseException:
            sock.close()
            raise

def probe(url: str,policy: dict,remaining: dict) -> dict:
    hops=0
    while True:
        host,path=inspect_url(url,policy)
        if remaining["requests"]<=0:
            raise NetworkPolicyError("Request budget exhausted")
        family,sockaddr=pinned_address(host)
        remaining["requests"]-=1
        connection=PinnedConnection(host,(family,sockaddr),policy["timeout_seconds"])
        try:
            connection.request("GET",path+("?" + urlsplit(url).query if urlsplit(url).query else ""),
                               headers={"Range":"bytes=0-65535","Connection":"close",
                                        "User-Agent":"foo-doujinstyle-e2e-transport/1"})
            resp=connection.getresponse()
            if resp.status in REDIRECTS:
                if hops>=policy["max_redirects"]:
                    raise NetworkPolicyError("Redirect budget exhausted")
                location=resp.getheader("Location")
                if not location or len(location)>4096:
                    raise NetworkPolicyError("Invalid redirect location")
                next_url=urljoin(url,location)
                inspect_url(next_url,policy)
                url=next_url
                hops+=1
                continue
            remaining_size=min(policy["max_response_bytes"],remaining["bytes"])
            header=resp.getheader("Content-Length")
            if header and header.isdecimal() and int(header)>remaining_size:
                raise NetworkPolicyError("Response exceeds approved byte budget")
            hasher=sha256()
            received=0
            while True:
                chunk=resp.read(min(65536,remaining_size-received+1))
                if not chunk:
                    break
                received+=len(chunk)
                if received>remaining_size:
                    raise NetworkPolicyError("Response exceeds approved byte budget")
                hasher.update(chunk)
            remaining["bytes"]-=received
            return {"status":resp.status,"redirect_hops":hops,"body_bytes":received,
                    "body_sha256":hasher.hexdigest(),
                    "qualification":"allowlisted-tls-transport-only-not-host-playback"}
        finally:
            connection.close()

def run(policy: dict,cases: list[dict],cases_sha256: str) -> dict:
    validate_policy(policy)
    if not policy["enabled"]:
        raise NetworkPolicyError("Real network disabled by policy")
    if not isinstance(cases,list) or not 1<=len(cases)<=policy["max_requests"]:
        raise NetworkPolicyError("Invalid bounded test case count")
    used=set()
    budget={"requests":policy["max_requests"],"bytes":policy["max_total_bytes"]}
    rows=[]
    for row in cases:
        if not isinstance(row,dict) or set(row)!={"id","url"} or not CASE_ID.fullmatch(row["id"]) or row["id"] in used:
            raise NetworkPolicyError("Bad or repeated case id")
        used.add(row["id"])
        try:
            observation=probe(row["url"],policy,budget)
            rows.append({"case_id":row["id"],"status":"observed","response":observation})
        except (NetworkPolicyError,TimeoutError,OSError,ssl.SSLError,http.client.HTTPException):
            # Fixed code only. Never print arbitrary URLs, DNS answers or TLS errors.
            rows.append({"case_id":row["id"],"status":"failed","reason":"blocked_or_failed_request"})
    return {"schema_version":1,"qualification":"real-transport-only-not-foobar-host",
            "case_manifest_sha256":cases_sha256,"cases":rows,
            "requests_used":policy["max_requests"]-budget["requests"],
            "body_bytes":policy["max_total_bytes"]-budget["bytes"],
            "host_e2e_qualified":False,"native_coverage":None}

def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--policy",type=Path,required=True)
    ap.add_argument("--cases",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--confirm-isolated-network-profile",action="store_true")
    a=ap.parse_args()
    try:
        if not a.confirm_isolated_network_profile:
            raise NetworkPolicyError("External egress firewall approval required")
        if a.output.exists():
            raise NetworkPolicyError("Refusing to overwrite test evidence")
        policy=json.loads(a.policy.read_text(encoding="utf-8"))
        data=a.cases.read_bytes()
        result=run(policy,json.loads(data),sha256(data).hexdigest())
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        return 0 if all(c["status"]=="observed" for c in result["cases"]) else 1
    except (OSError,ValueError,TypeError,json.JSONDecodeError):
        print("Network profile refused (invalid config/permissions/inputs)",file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
