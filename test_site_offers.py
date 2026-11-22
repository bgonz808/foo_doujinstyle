"""Cover the coordinator and compare WinHTTP and Python with a loopback release."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from doujin import Http, parse_detail, resolve_offer
from native.reference_publisher import NativeCatalogWorkerStartupFailed, NativeCatalogWorkerUnavailable, ReferencePublisher
from native.search_worker import fetch_offer


PAGE = """<mainbar><h2>Fixture release</h2>
<form method="get" action="/ignored">
<input type="hidden" name="id" value="25"><input type="hidden" name="type" value="1">
<input type="hidden" name="source" value="3">
<button name="download_link" formaction="/offer" formmethod="post" value="夜 +&amp;">Download</button>
</form></mainbar>""".encode()
MIRROR_PAGE = PAGE.replace(b"</mainbar>", PAGE[PAGE.index(b"<form"):].replace(b'value="3"', b'value="5"'))


class OfferSessionTests(unittest.TestCase):
    url = "https://doujinstyle.com/?p=page&type=1&id=25"
    supported = "https://www.mediafire.com/file/abc/music.zip"

    def setUp(self):
        row, forms = parse_detail(MIRROR_PAGE, 25, self.url)
        self.page = {"row": row, "forms": forms}
        self.http = Mock()
        self.publisher = Mock()

    def test_native_offer_uses_one_request_and_keeps_catalog_evidence(self):
        details = {**self.page["row"], "download_forms": self.page["forms"]}
        offer = {"host": "www.mediafire.com", "filename": "[ＴＥＳＴ-25] album.zip", "format_hint": "zip"}
        self.publisher.request.return_value = {
            "observation": details, "offers": [["doujinstyle:3", offer, self.supported]], "error": None,
        }
        _, observation, offers, error = fetch_offer(
            self.http, {"id": 25, "url": self.url}, self.publisher, all_forms=True)
        self.assertEqual(observation, details)
        self.assertEqual(offers[0][1]["catalog_id_candidate"], "TEST-25")
        self.assertIsNone(error)
        self.publisher.request.assert_called_once_with(None, "site_release_offers", id=25, url=self.url, all_forms=True)
        self.http.get.assert_not_called()

    def test_native_get_failure_can_fall_back_but_post_failure_cannot_replay(self):
        for outcome in (NativeCatalogWorkerStartupFailed("native startup failed"), {"retry_with_python": True}):
            with self.subTest(outcome=outcome):
                self.publisher.request.side_effect = [outcome]
                self.http.get.side_effect = [({}, PAGE, self.url), ({"Location": self.supported}, b"", self.url)]
                _, _, offers, error = fetch_offer(self.http, {"id": 25, "url": self.url}, self.publisher)
                self.assertIsNone(error)
                self.assertEqual(offers[0][2], self.supported)
                self.assertTrue(self.http.get.call_args.kwargs["data"].startswith(b"id=25&type=1&source=3"))

        self.http.reset_mock()
        self.publisher.request.side_effect = OSError("offer response lost")
        _, observation, offers, error = fetch_offer(self.http, {"id": 25, "url": self.url}, self.publisher)
        self.assertIsNone(observation)
        self.assertEqual(offers, [])
        self.assertEqual(error, "offer response lost")
        self.http.get.assert_not_called()

    def test_rate_limit_does_not_retry_get_in_python(self):
        self.publisher.request.return_value = {"status": 429}
        _, observation, offers, error = fetch_offer(self.http, {"id": 25, "url": self.url}, self.publisher)
        self.assertIsNone(observation)
        self.assertEqual(offers, [])
        self.assertIn("HTTP 429", error)
        self.http.get.assert_not_called()

    def test_lost_peer_is_reopened_only_for_the_next_request(self):
        original, replacement = Mock(), Mock()
        for process in (original, replacement):
            process.wait.return_value = 0
        original.stdout.readline.side_effect = [b"DoujinStyle catalog 1\n", b""]
        replacement.stdout.readline.side_effect = [b"DoujinStyle catalog 1\n", b'{"result": {}}\n']
        with patch("native.reference_publisher.subprocess.Popen", side_effect=[original, replacement]) as start:
            worker = ReferencePublisher("fixture.exe")
            self.addCleanup(worker.close)
            with self.assertRaises(NativeCatalogWorkerUnavailable):
                worker.request(None, "site_release_offers", id=25)
            start.assert_called_once()
            original.stdin.write.assert_called_once()
            original.stdin.close.assert_called_once()
            self.assertEqual(worker.request(None, "site_release_offers", id=26), {})
            self.assertEqual(start.call_count, 2)
            replacement.stdin.write.assert_called_once()


class LoopbackOfferHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/release?id=25")
            self.send_header("Set-Cookie", "step=redirect; Path=/")
            self.end_headers()
        elif self.path == "/release?id=25" and self.headers.get("Cookie") == "step=redirect":
            self.send_response(200)
            self.send_header("Set-Cookie", "step=release; Path=/")
            self.send_header("Referrer-Policy", self.server.policy)
            self.end_headers()
            self.wfile.write(MIRROR_PAGE if getattr(self.server, "stall_primary", False) else PAGE)
        else:
            self.send_error(403)

    def do_POST(self):
        if self.path != "/offer":
            self.send_error(404)
            return
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.posts.append((self.headers.get("Cookie"), self.headers.get("Origin"),
                                  self.headers.get("Referer"), body))
        if getattr(self.server, "stall_primary", False) and b"source=3&" in body:
            self.server.release_primary.wait(20)
            return
        self.send_response(302)
        value = "https://www.mediafire.com/file/abc/Album+〜 Mix.zip/file?x=%2B&name=曲"
        self.send_header("Location", value.encode().decode("latin-1"))
        self.end_headers()

    def log_message(self, *_):
        pass


class WinHttpOfferFixture(unittest.TestCase):
    def test_python_loopback_baseline(self):
        self.check_session()

    @unittest.skipUnless(os.getenv("DOUJINSTYLE_TEST_SITE_SESSION"), "Set the compiled Windows offer fixture")
    def test_winhttp_carries_get_cookies_into_a_single_offer_post(self):
        self.check_session(os.environ["DOUJINSTYLE_TEST_SITE_SESSION"])

    @unittest.skipUnless(os.getenv("DOUJINSTYLE_TEST_SITE_SESSION"), "Set the compiled Windows offer fixture")
    def test_winhttp_timeout_does_not_post_the_primary_again(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), LoopbackOfferHandler) as server:
            server.policy, server.posts, server.stall_primary = "same-origin", [], True
            server.release_primary = threading.Event()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = self.run_native(os.environ["DOUJINSTYLE_TEST_SITE_SESSION"],
                                         f"http://127.0.0.1:{server.server_port}/start")
                self.assertIn("12002", result["error"])
                self.assertEqual([source for source, _, _ in result["offers"]], ["doujinstyle:5"])
                self.assertEqual([post[3].split(b"&")[2] for post in server.posts], [b"source=3", b"source=5"])
                self.assertEqual([post[0] for post in server.posts], ["step=release"] * 2)
            finally:
                server.release_primary.set()
                server.shutdown()
                thread.join()

    def run_native(self, executable, url):
        wine = os.getenv("DOUJINSTYLE_TEST_WINE")
        command = [wine, executable, url] if wine else [executable, url]
        run = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def check_session(self, executable=None):
        with ThreadingHTTPServer(("127.0.0.1", 0), LoopbackOfferHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                url = origin + "/start"
                for policy, expected_origin, referer in (
                    ("same-origin", origin, origin + "/release?id=25"),
                    ("no-referrer", "null", None),
                ):
                    with self.subTest(policy=policy):
                        server.policy, server.posts = policy, []
                        python = Http()
                        headers, page, effective = python.get(url)
                        row, forms = parse_detail(page, 25, effective, headers)
                        _, share = resolve_offer(python, row, forms[0])
                        self.assertEqual(share, "https://www.mediafire.com/file/abc/"
                                               "Album+%E3%80%9C%20Mix.zip/file?x=%2B&name=%E6%9B%B2")
                        expected_body = b"id=25&type=1&source=3&download_link=%E5%A4%9C+%2B%26"
                        expected = ("step=release", expected_origin, referer, expected_body)
                        self.assertEqual(server.posts, [expected])
                        if executable is not None:
                            result = self.run_native(executable, url)
                            self.assertIsNone(result["error"])
                            self.assertEqual([offer[2] for offer in result["offers"]], [share])
                            self.assertEqual(server.posts, [expected, expected])
            finally:
                server.shutdown()
                thread.join()
