import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from e2e.host.build_receipt import create,verify,MEMBER
from e2e.host.runner import sha256,QualificationError

class BuildReceiptContracts(unittest.TestCase):
    def test_main_exact_source_binding(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            package=root/"test-e2e-trace.fb2k-component"
            dll=root/"foo_doujinstyle.dll";dll.write_bytes(b"fixture")
            exporter=root/"etw_export.exe";exporter.write_bytes(b"synthetic exporter")
            with ZipFile(package,"w") as z:z.writestr(MEMBER,dll.read_bytes())
            receipt=root/"receipt.json"
            d=create(package,exporter,dll,"a"*40,"0.0.272","123","1","refs/heads/main")
            receipt.write_text(json.dumps(d))
            self.assertEqual(verify(receipt,sha256(receipt),package,exporter,"a"*40),d)
            for digest,source in (("b"*64,"a"*40),(sha256(receipt),"c"*40)):
                with self.assertRaises(QualificationError):verify(receipt,digest,package,exporter,source)
            d["ref"]="refs/pull/1/merge";receipt.write_text(json.dumps(d))
            with self.assertRaises(QualificationError):verify(receipt,sha256(receipt),package,exporter,"a"*40)
            d["ref"]="refs/heads/main";receipt.write_text(json.dumps(d))
            exporter.write_bytes(b"changed")
            with self.assertRaises(ValueError):verify(receipt,sha256(receipt),package,exporter,"a"*40)
