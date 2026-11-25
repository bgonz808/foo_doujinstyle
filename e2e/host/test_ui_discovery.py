import copy
import unittest
from e2e.host.ui_discovery import validate,candidates
from e2e.host.runner import QualificationError

def node(i,parent,depth,aid="",password=False):
    return dict(index=i,parent=parent,depth=depth,pid=42,name="" if password else "synthetic",
                automation_id=aid,control_type="ControlType.Button",class_name="Button",
                framework_id="Win32",enabled=True,offscreen=False,password=password,
                keyboard_focusable=True,bounds=[0,0,100,20],patterns=["InvokePatternIdentifiers.Pattern"])

class UiDiscoveryContracts(unittest.TestCase):
    def setUp(self):
        self.doc=dict(schema_version=1,kind="uia-control-view",pid=42,truncated=False,
                      excluded_foreign_subtrees=0,nodes=[node(0,-1,0),node(1,0,1,"search"),node(2,0,1,"dup"),node(3,0,1,"dup")])

    def test_candidates_unique_per_window(self):
        self.assertEqual(len(validate(self.doc,42)),4)
        self.assertEqual([x["automation_id"] for x in candidates(self.doc,42)["selector_candidates"]],["search"])
        self.assertFalse(candidates(self.doc,42)["runtime_uniqueness_verified"])

    def test_truncated_tree_has_no_unique_claim(self):
        self.doc["truncated"]=True
        self.assertEqual(candidates(self.doc,42)["selector_candidates"],[])

    def test_reject_foreign_pid_hierarchy_password_and_extra_fields(self):
        for key,value in (("pid",43),("parent",1),("depth",3),("password",True),
                          ("bounds",[0,0,float("nan"),1]),("value","secret"),("control_type","ControlType.Pane"),("control_type","ControlType.Edit")):
            d=copy.deepcopy(self.doc)
            d["nodes"][1][key]=value
            with self.subTest(key=key),self.assertRaises(QualificationError):
                validate(d,42)
