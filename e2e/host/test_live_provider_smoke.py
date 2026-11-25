import json
import unittest
from e2e.host.live_provider_smoke import archive_projection,chart_ids,mediafire_url

class LiveProviderContracts(unittest.TestCase):
    def test_bounded_unique_selection(self):
        self.assertEqual(chart_ids([{"releases":[{"id":i} for i in [1,1,2,3,4,5,6,7]]}]),[1,2,3,4,5,6])
    def test_projection_never_exports_raw_inventory_or_links(self):
        output=archive_projection({"format":"zip","archive_bytes":1234,"inventory":[{"filename":"private.flac","compress_type":8,"url":"https://private.invalid/key"}],"archive_http":{"requests":2,"body_bytes":200,"secret":"token"}})
        text=json.dumps(output)
        self.assertNotIn("private",text)
        self.assertNotIn("token",text)
        self.assertEqual(output["compression_methods"],{"8":1})
    def test_mediafire_family(self):
        self.assertTrue(mediafire_url("https://download123.mediafire.com/path"))
        for url in ("https://mediafire.com.evil.invalid/path","http://mediafire.com/path","https://user@mediafire.com/path","https://mega.nz/file#key"):
            self.assertFalse(mediafire_url(url))
