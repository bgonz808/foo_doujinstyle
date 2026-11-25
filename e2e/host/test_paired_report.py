import unittest
from e2e.host.paired_report import stage_offsets

class PairedReportContracts(unittest.TestCase):
    def test_missing_and_ambiguous_stages_not_zero(self):
        def ev(name,source,pid,tick):
            return dict(event=name,source=source,pid=pid,run_id="a"*32,scenario_id="fixtureplay",
                attempt_id="fixtureplay-0001",clock=dict(domain="win-qpc:"+"a"*32,tick=tick,frequency_hz=10000000))
        start=ev("robot.play.intent","robot",1,100)
        common=ev("component.observer.first_pcm","component",2,200)
        stage=ev("component.decoder.initialize.begin","component",2,150)
        result=stage_offsets([start,common,stage,stage],2)
        self.assertEqual(result["component.observer.first_pcm"]["offset_ns"],10000)
        self.assertIsNone(result["component.decoder.initialize.begin"]["offset_ns"])
        self.assertIsNone(result["output.pcm.first"]["offset_ns"])
        self.assertIsNone(stage_offsets([start,common],3)["component.observer.first_pcm"]["offset_ns"])


class FullPairedReportContracts(unittest.TestCase):
    """Exercise actual reporter with synthetic evidence, never a host pass."""
    def setUp(self):
        import tempfile
        from pathlib import Path
        from e2e.host.fixture_assets import make_fixture,extract_local_control,write_reference
        from e2e.host.runner import sha256
        from e2e.host.paired_report import PROFILES
        td=tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root=Path(td.name)
        media,assets=make_fixture()
        self.ready=dict(paired_common_pcm_requested=True,source_commit="a"*40,
                        component_sha256="b"*64,etw_exporter_sha256="c"*64,profiles=[])
        for i,profile in enumerate(PROFILES):
            folder=self.root/profile
            fixture=folder/"fixture";fixture.mkdir(parents=True)
            robot=folder/"robot";robot.mkdir()
            trace=folder/"trace";trace.mkdir()
            if i==0:extract_local_control(fixture,assets,media)
            else:write_reference(fixture,"http://127.0.0.1:12345",profile,media,
                                 media if i==1 else assets[profile+".zip"])
            run=f"{i+1:032x}"
            pid=200+i
            self.write(robot/"cli-host.json",dict(run_id=run,pid=pid))
            self.write(fixture/"http-requests.json",[] if not i else
                       [dict(status=206 if i>=2 else 200,bytes_sent=64)])
            def write_events(path,source,process,items):
                import json
                with path.open("w",encoding="utf-8") as stream:
                    for seq,(name,tick,metrics) in enumerate(items):
                        d=dict(schema_version=1,source=source,pid=process,seq=seq,
                               run_id=run,scenario_id="fixtureplay",attempt_id="fixtureplay-0001",
                               event=name,clock=dict(domain="win-qpc:"+run,tick=tick,frequency_hz=10000000),
                               metrics=metrics)
                        stream.write(json.dumps(d)+"\n")
            write_events(trace/"robot.jsonl","robot",101,[
                ("clock.sync",10,{}),("robot.host.launch.intent",20,{}),
                ("robot.play.intent",100,{}),("robot.play.dispatch.return",105,{})])
            events=[("component.observer.config_ready",50,{})]
            if i:
                events += [("component.track.open.begin",110,{}),
                           ("component.decoder.first_pcm",130,{"sample_count":480})]
            events += [("component.observer.first_pcm",200+100*i,{"sample_count":480})]
            write_events(trace/"component.jsonl","component",pid,events)
            ck=dict(status="passed",profile=profile,cli_only=True,run_id=run,
                    playback_method="foobar2000_cli_add_then_play_ipc",
                    output_backend_verified="null_sdk_core_config",source_commit="a"*40,
                    etw_exporter_sha256="c"*64,foobar_version="2.0",
                    template=dict(tree_sha256="d"*64,host_sha256="e"*64,component_sha256="b"*64),
                    cache_state="fresh_portable_copy_new_host_os_cache_unverified",
                    cli_host_evidence_sha256=sha256(robot/"cli-host.json"),
                    trace_evidence_sha256={p.name:sha256(p) for p in trace.glob("*.jsonl")},
                    fixture_manifest_sha256=sha256(fixture/"fixture-manifest.json"),
                    fixture_http_sha256=sha256(fixture/"http-requests.json"))
            self.write(folder/"checkpoint.json",ck)
            self.ready["profiles"].append(dict(profile=profile,status="passed",
                        checkpoint_sha256=sha256(folder/"checkpoint.json")))
        self.write(self.root/"readiness.json",self.ready)

    @staticmethod
    def write(path,doc):
        import json
        path.write_text(json.dumps(doc),encoding="utf-8")

    def test_full_report(self):
        from e2e.host.paired_report import report
        result=report(self.root)
        self.assertEqual([a["delta_vs_local_ns"] for a in result["arms"]],[0,10000,20000,30000])
        self.assertFalse(result["actual_output_measured"])
        self.assertFalse(result["statistical_regression_qualified"])

    def test_local_member_mutation(self):
        from e2e.host.paired_report import report
        from e2e.host.runner import QualificationError
        (self.root/"fixture-local"/"fixture"/"local-control.wav").write_bytes(b"changed")
        with self.assertRaises(QualificationError):report(self.root)

    def test_archive_locator_mismatch_even_if_rehashed(self):
        import json
        from e2e.host.paired_report import report
        from e2e.host.runner import sha256,QualificationError
        folder=self.root/"fixture-zip-deflate"
        path=folder/"fixture"/"fixture-manifest.json"
        m=json.loads(path.read_text());m["member_header_offset"]+=1
        self.write(path,m)
        ck=folder/"checkpoint.json"
        d=json.loads(ck.read_text());d["fixture_manifest_sha256"]=sha256(path)
        self.write(ck,d)
        self.ready["profiles"][3]["checkpoint_sha256"]=sha256(ck)
        self.write(self.root/"readiness.json",self.ready)
        with self.assertRaises(QualificationError):report(self.root)

    def test_trigger_mismatch_even_if_rehashed(self):
        import json
        from e2e.host.paired_report import report
        from e2e.host.runner import sha256,QualificationError
        ck=self.root/"fixture-direct"/"checkpoint.json"
        d=json.loads(ck.read_text());d["playback_method"]="uia_invoke"
        self.write(ck,d)
        self.ready["profiles"][1]["checkpoint_sha256"]=sha256(ck)
        self.write(self.root/"readiness.json",self.ready)
        with self.assertRaises(QualificationError):report(self.root)

    def test_repeated_trials_and_duplicate_rejection(self):
        import copy
        from e2e.host.paired_report import report,summarize_trials
        from e2e.host.runner import QualificationError
        a=report(self.root)
        b=copy.deepcopy(a)
        for i,row in enumerate(b["arms"]):
            row["run_id"]=f"{100+i:032x}"
        summary=summarize_trials([a,b],5)
        self.assertEqual(summary["completed_trials"],2)
        self.assertEqual(summary["status"],"incomplete")
        self.assertEqual(summary["summary"]["fixture-zip-store"]["delta_vs_local_ns"]["median"],20000)
        self.assertEqual(summary["summary"]["fixture-local"]["http_body_bytes"]["values"],[0,0])
        with self.assertRaises(QualificationError):
            summarize_trials([a,a],5)
        b["arms"][0]["audio_sha256"]="f"*64
        with self.assertRaises(QualificationError):
            summarize_trials([a,b],5)
