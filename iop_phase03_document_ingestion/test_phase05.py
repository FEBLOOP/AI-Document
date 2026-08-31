from phase05.retrieval import retrieve
from phase05.rubric import load_criteria


def test_retrieval_returns_matching_chunk():
    hits = retrieve([{"chunk_id": "C1", "normalized_text": "นโยบายด้านนวัตกรรมและผู้บริหาร"}, {"chunk_id": "C2", "normalized_text": "การเงินบัญชี"}], "ผู้บริหาร นวัตกรรม", 1)
    assert hits[0]["chunk"]["chunk_id"] == "C1"


def test_iop_hierarchy_is_flattened():
    rubric = {"dimensions": [{"id": "D1", "components": [{"id": "1.1", "criteria": [{"id": "1.1.1", "description": "x"}]}]}]}
    assert load_criteria(rubric)[0]["criterion_id"] == "1.1.1"
