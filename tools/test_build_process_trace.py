from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tools.build_process_trace import process_tree, sanitize_tree, sequence, command_evidence, local_path, parse_events

def event(kind,t,pid,parent=0,image="",seq="",parent_seq=""):
    return {"id":kind,"time":t,"pid":pid,"parent":parent,"image":image,"sequence":seq,
            "parent_sequence":parent_seq,"created":"","command":"","exit":"0"}

class BuildProcessTraceTests(unittest.TestCase):
    def test_lifetime_pid_reuse_and_unrelated_process(self):
        events=[event(1,0,10,image="msbuild.exe",seq="1"),
                event(1,1,11,10,"cl.exe","2","1"),
                event(5,2,11,image=r"C:\private\c2.dll"),
                event(2,3,11,seq="2"),
                event(1,4,11,99,"cl.exe","3"),
                event(5,5,11,image=r"C:\unrelated\bad.dll"),
                event(2,6,10,seq="1")]
        tree,unassigned=process_tree(events,10)
        self.assertEqual(len(tree),2)
        self.assertEqual(unassigned,1)
        result=sanitize_tree(tree,{})
        self.assertEqual(result["peak_observed_cl_processes"],1)
        self.assertEqual(result["process_counts"],{"msbuild.exe":1,"cl.exe":1})
        self.assertNotIn("private",str(result))
        self.assertNotIn("unrelated",str(result))

    def test_sequence_and_path_mapping(self):
        for replica in ("a","b"):
            self.assertEqual(set(Counter(sequence(replica)).values()),{2})
        devices={r"\device\harddiskvolume3":"C:"}
        self.assertEqual(local_path(r"\Device\HarddiskVolume3\Windows\x.dll",devices),r"C:\Windows\x.dll")
        self.assertIsNone(local_path(r"\\server\share\x.dll",devices))

    def test_command_evidence_is_not_argv(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"log.txt"
            path.write_text(r'"C:\private\cl.exe" /Brepro /Qspectre /Z7 /pathmap:C:\secret=X:\canonical sqlite_amalgamation.c',encoding="utf-8")
            report=command_evidence(path)
            self.assertEqual(len(report["compiler_command_observations"]),1)
            self.assertTrue(report["compiler_command_observations"][0]["pathmap_observed"])
            self.assertNotIn("secret",str(report))
            self.assertNotIn("private",str(report))

    def test_real_manifest_shape(self):
        xml='''<Events><Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
          <System><Provider Name="Microsoft-Windows-Kernel-Process"/><EventID>1</EventID>
          <TimeCreated SystemTime="2026-11-27T10:00:00.1234567Z"/></System>
          <EventData><Data Name="ProcessID">0xA</Data><Data Name="ParentProcessID">3</Data>
          <Data Name="ProcessSequenceNumber">1</Data><Data Name="ImageName">msbuild.exe</Data></EventData></Event></Events>'''
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/"trace.xml"
            p.write_text(xml)
            rows=parse_events(p)
            self.assertEqual(rows[0]["pid"],10)
            self.assertEqual(rows[0]["command"],"")
            p.write_text(xml.replace('xmlns="http://schemas.microsoft.com/win/2004/08/events/event"',''))
            self.assertEqual(parse_events(p)[0]["pid"],10)
            p.write_text(xml.replace('</EventData>','<Event><Data Name="ignored">x</Data></Event></EventData>'))
            self.assertEqual(len(parse_events(p)),1)
            p.write_text('<Events><Event/></Events>')
            with self.assertRaisesRegex(ValueError,"Missing event System"):
                parse_events(p)
