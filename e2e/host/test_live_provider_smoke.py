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

    def test_range_union_and_first_pcm_cutoff(self):
        from e2e.host.live_provider_smoke import io_summary
        rows=[{"status":206,"complete":True,"range_first":a,"range_last":b,"body_bytes":b-a+1,"resource_size":100,"end_qpc":end,"qpc_frequency":10} for a,b,end in [(0,9,10),(5,14,20),(90,99,30)]]
        summary=io_summary(rows,20,10)
        self.assertEqual(summary["unique_archive_bytes"],15)
        self.assertEqual(summary["duplicate_archive_bytes"],5)
        self.assertEqual(summary["incomplete_or_after_boundary"],1)
        self.assertEqual(io_summary(rows)["unique_archive_bytes"],25)

    def test_balanced_selection_and_artwork(self):
        from e2e.host.live_provider_smoke import balanced_ids,artwork_inventory,artwork_probe_projection
        self.assertEqual(balanced_ids([[1,2,3],[2,4],[5,6]],5),[1,2,5,4,6])
        result=artwork_inventory({"inventory":[{"kind":"audio_candidate","path":"disc/a.flac"},
            {"kind":"artwork","path":"disc/front.jpg","uncompressed_bytes":20},
            {"kind":"artwork","path":"cover.jpg","uncompressed_bytes":30}]})
        self.assertEqual(result["audio_with_same_folder_artwork"],1)
        self.assertEqual(result["audio_with_ancestor_artwork"],1)
        self.assertNotIn("front.jpg",json.dumps(result))
        p=artwork_probe_projection({"audio_probes":{"secret.flac":{"artwork":[{"source":"FLAC PICTURE"}]},"other.mp3":{}}})
        self.assertEqual([r["embedded_status"] for r in p["tracks"]],["observed","not_observed_in_prefix"])
        self.assertNotIn("secret.flac",json.dumps(p))
