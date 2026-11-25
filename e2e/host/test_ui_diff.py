import copy
import unittest
from e2e.host.test_ui_discovery import node
from e2e.host.ui_diff import diff
from e2e.host.runner import QualificationError

class UiDiffContracts(unittest.TestCase):
    def setUp(self):
        self.doc=dict(schema_version=1,kind="uia-control-view",pid=42,truncated=False,
            excluded_foreign_subtrees=0,nodes=[node(0,-1,0,"root"),node(1,0,1,"play"),node(2,0,1,"search")])
    def test_geometry_and_reordering_not_semantic(self):
        after=copy.deepcopy(self.doc)
        after["nodes"][1],after["nodes"][2]=after["nodes"][2],after["nodes"][1]
        for i,n in enumerate(after["nodes"]): n["index"]=i
        after["nodes"][1]["bounds"]=[20,30,100,20]
        result=diff(self.doc,after)
        self.assertEqual(result["semantic_changes"],[])
        self.assertEqual(result["added"],[])
        self.assertEqual(result["removed"],[])
        self.assertEqual(len(result["geometry_changes"]),1)
        self.assertFalse(result["review_required"])
    def test_semantic_loss_and_ambiguous_ids(self):
        after=copy.deepcopy(self.doc)
        after["nodes"][1]["patterns"]=[]
        self.assertTrue(diff(self.doc,after)["review_required"])
        after["nodes"][2]["automation_id"]="play"
        result=diff(self.doc,after)
        self.assertEqual(len(result["removed"]),2)
        self.assertEqual(result["unkeyed_after"],2)
        after["truncated"]=True
        with self.assertRaises(QualificationError): diff(self.doc,after)
