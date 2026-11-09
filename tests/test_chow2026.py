from cellertz.tools.protein_design.chow2026 import infer_target


def test_infer_bcma_target():
    assert infer_target("BCMA RFDiffusion Campaign 3") == "BCMA"


def test_infer_cd19_target():
    assert infer_target("CD19 BindCraft Campaign") == "CD19"


def test_infer_cd22_target():
    assert infer_target("CD22 Campaign") == "CD22"


def test_unknown_target():
    assert infer_target("Unknown Campaign") is None
