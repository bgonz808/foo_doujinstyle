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

    def test_member_selection_and_ranges(self):
        import tempfile
        from pathlib import Path
        from e2e.host.live_provider_smoke import select_member,verify_ranges,digest,profiles
        item={"path":"disc/a.flac","suffix":".flac","kind":"audio_candidate","zip_method":8,
              "zip_header_offset":0,"uncompressed_bytes":65536,"compressed_bytes":10,"crc32":"12345678"}
        m=select_member({"format":"zip","archive_bytes":100000,"inventory":[item]})
        self.assertEqual(m["raw_hex"],b"disc/a.flac".hex())
        self.assertIsNone(select_member({"format":"zip","archive_bytes":2**40,"inventory":[item]}))
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"archive";p.write_bytes(b"0123456789")
            row={"status":206,"complete":True,"range_sha256":digest(b"234"),"range_first":2,
                 "range_last":4,"resource_size":10,"body_bytes":3}
            self.assertEqual(verify_ranges(p,[row]),1)
            row["range_sha256"]="0"*64
            with self.assertRaises(ValueError): verify_ranges(p,[row])

    def test_profiles_preserve_service_role_and_connection_state(self):
        from e2e.host.live_provider_smoke import profiles
        row={"phase":"remote","host":"download123.mediafire.com","method":"GET","protocol":2,
            "connection_first":1,"status":206,"headers_us":20,"first_body_us":25,"last_body_us":50,
            "elapsed_us":80,"body_bytes":1024,"read_calls":2,"dns_us":1,"connect_us":2,
            "tls_us":3,"request_to_headers_us":10}
        results=profiles([row,{**row,"connection_first":0},{**row,"phase":"download"}])
        self.assertEqual(len(results),3)
        self.assertTrue(all(x["service_role"]=="hoster_storage" and x["body_read_span_us"]==[25] for x in results))
