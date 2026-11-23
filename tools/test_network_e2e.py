"""Network E2E DNS, TLS/redirect and byte budgets; all tests are offline mocks."""
import socket
import unittest
from unittest.mock import patch
from tools.network_e2e import NetworkPolicyError,pinned_address,inspect_url,validate_policy,run

def policy():
    return {"schema_version":1,"enabled":True,"hosts":["safe.example.com"],
            "max_requests":3,"max_total_bytes":4096,"max_response_bytes":1024,
            "timeout_seconds":3,"max_redirects":1}

class NetworkContractTests(unittest.TestCase):
    def test_default_disabled(self):
        p=policy();p["enabled"]=False
        with self.assertRaisesRegex(NetworkPolicyError,"disabled"):
            run(p,[{"id":"case-001","url":"https://safe.example.com/a"}],"a"*64)

    def test_cannot_allow_ip_wildcard_or_private_host(self):
        for host in ["127.0.0.1","*.example.com","home.local","a..b.com",
                     "a.TEST","[::1]","internal","foo.internal"]:
            with self.subTest(host=host):
                p=policy();p["hosts"]=[host]
                with self.assertRaises(NetworkPolicyError):
                    validate_policy(p)

    def test_url_requires_exact_hostname_https_and_no_credentials(self):
        p=policy()
        for url in ["http://safe.example.com/","https://safe.example.com.evil.net",
                    "https://evil@safe.example.com/","https://safe.example.com:444/",
                    "https://127.0.0.1/","https://safe.example.com/a#fragment",
                    "https://SAFE.example.com/","https://safe.example.com./"]:
            with self.subTest(url=url),self.assertRaises(NetworkPolicyError):
                inspect_url(url,p)
        self.assertEqual(inspect_url("https://safe.example.com/a?query=secret",p),
                         ("safe.example.com","/a"))

    def test_dns_private_and_mixed_dns_answers_rejected(self):
        with patch("tools.network_e2e.socket.getaddrinfo") as get:
            for ip in ["10.2.3.4","192.168.1.1","127.0.0.1","169.254.1.1",
                       "100.64.1.5","::1","fe80::1234"]:
                fam=socket.AF_INET6 if ":" in ip else socket.AF_INET
                get.return_value=[(fam,socket.SOCK_STREAM,6,"",(ip,443))]
                with self.subTest(ip=ip),self.assertRaises(NetworkPolicyError):
                    pinned_address("safe.example.com")
            get.return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443)),
                              (socket.AF_INET,socket.SOCK_STREAM,6,"",("10.1.1.1",443))]
            with self.assertRaises(NetworkPolicyError):
                pinned_address("safe.example.com")
            get.return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443))]
            self.assertEqual(pinned_address("safe.example.com")[1][0],"8.8.8.8")

    def test_no_secret_in_reports(self):
        with patch("tools.network_e2e.probe",return_value={
            "status":206,"redirect_hops":0,"body_bytes":7,"body_sha256":"0"*64,
            "qualification":"allowlisted-tls-transport-only-not-host-playback"}):
            report=run(policy(),[{"id":"case-001","url":"https://safe.example.com/private/token=SECRET"}],"a"*64)
        self.assertNotIn("SECRET",str(report))
        self.assertFalse(report["host_e2e_qualified"])
        self.assertIsNone(report["native_coverage"])

    def test_stream_failure_debits_actual_bytes_without_overread(self):
        from tools.network_e2e import probe
        class FakeResponse:
            status=200
            def __init__(self):
                self.data=b"x"*3000
                self.pos=0
            def getheader(self,name):
                return None
            def read(self,n):
                chunk=self.data[self.pos:self.pos+n]
                self.pos+=len(chunk)
                return chunk
        response=FakeResponse()
        class FakeConnection:
            def __init__(self,*args):pass
            def request(self,*args,**kwargs):pass
            def getresponse(self):return response
            def close(self):pass
        p=policy()
        budget={"requests":2,"bytes":4096}
        with patch("tools.network_e2e.pinned_address",return_value=(socket.AF_INET,("8.8.8.8",443))), \
             patch("tools.network_e2e.PinnedConnection",FakeConnection):
            with self.assertRaisesRegex(NetworkPolicyError,"byte budget"):
                probe("https://safe.example.com/a",p,budget)
        self.assertEqual(response.pos,1024)
        self.assertEqual(budget["bytes"],4096-1024)

    def test_disabled_or_overlarge_budgets_rejected(self):
        for change in [{"max_requests":33},{"max_redirects":8},
                       {"max_total_bytes":2},{"max_response_bytes":99999999}]:
            p=policy();p.update(change)
            with self.assertRaises(NetworkPolicyError):
                validate_policy(p)

if __name__=="__main__":
    unittest.main()
