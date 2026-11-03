import copy

import pytest

from phase07.scoring import calculate_scoring, maturity_for_score


COUNTS = {"D1": 6, "D2": 6, "D3": 6, "D4": 7, "D5": 5, "D6": 7, "D7": 6, "D8": 6}


def _rubric(counts=COUNTS):
    return {
        "criteria": [
            {"criterion_id": f"{dimension}-{index}", "dimension_id": dimension}
            for dimension, count in counts.items()
            for index in range(1, count + 1)
        ]
    }


def _phase06(scores, counts=COUNTS):
    dimensions = []
    offset = 0
    for dimension, count in counts.items():
        criteria = []
        for index in range(1, count + 1):
            criteria.append(
                {
                    "criterion_id": f"{dimension}-{index}",
                    "status": "assessed",
                    "score": scores[offset],
                }
            )
            offset += 1
        dimensions.append({"dimension_id": dimension, "criterion_summaries": criteria})
    return {"dimension_summaries": dimensions}


def test_dimension_and_overall_scores_use_all_49_criteria():
    scores = [3] * 49
    result = calculate_scoring(_phase06(scores), _rubric())

    assert result["status"] == "complete"
    assert result["total_criteria"] == 49
    assert result["overall_score_raw"] == 3.0
    assert result["dimension_scores"] == {dimension: 3.0 for dimension in COUNTS}
    assert result["maturity_level"] == "Defined"


def test_overall_is_not_equal_weight_average_of_dimension_averages():
    counts = {"D1": 1, "D2": 3}
    phase06 = _phase06([1, 5, 5, 5], counts)
    result = calculate_scoring(phase06, _rubric(counts))

    assert result["overall_score_raw"] == 4.0
    assert sum(result["dimension_scores"].values()) / 2 == 3.0


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.0, "Initial"),
        (1.74, "Initial"),
        (1.75, "Managed"),
        (2.54, "Managed"),
        (2.55, "Defined"),
        (3.34, "Defined"),
        (3.35, "Performed"),
        (4.14, "Performed"),
        (4.15, "Mature"),
        (5.0, "Mature"),
    ],
)
def test_maturity_boundaries_use_configurable_one_decimal_rounding(score, expected):
    level, _, _ = maturity_for_score(score)
    assert level == expected


def test_missing_score_makes_overall_incomplete_without_imputation():
    phase06 = _phase06([3] * 49)
    phase06["dimension_summaries"][0]["criterion_summaries"].pop()
    result = calculate_scoring(phase06, _rubric())

    assert result["status"] == "incomplete"
    assert result["overall_score_raw"] is None
    assert result["maturity_level"] is None
    assert result["missing_criterion_ids"] == ["D1-6"]


def test_duplicate_criterion_is_reported():
    phase06 = _phase06([3] * 49)
    duplicate = copy.deepcopy(phase06["dimension_summaries"][0]["criterion_summaries"][0])
    phase06["dimension_summaries"][0]["criterion_summaries"].append(duplicate)
    result = calculate_scoring(phase06, _rubric())

    assert result["status"] == "incomplete"
    assert result["duplicate_criterion_ids"] == ["D1-1"]


@pytest.mark.parametrize("bad_score", [0, 6, 2.5, None, True])
def test_invalid_score_is_rejected_from_complete_result(bad_score):
    phase06 = _phase06([3] * 49)
    phase06["dimension_summaries"][0]["criterion_summaries"][0]["score"] = bad_score
    result = calculate_scoring(phase06, _rubric())

    assert result["status"] == "incomplete"
    assert result["overall_score_raw"] is None
    assert result["invalid_score_criterion_ids"] == ["D1-1"]


def test_pending_assessment_blocks_complete_result():
    phase06 = _phase06([3] * 49)
    phase06["dimension_summaries"][0]["criterion_summaries"][0]["status"] = "pending"
    result = calculate_scoring(phase06, _rubric())

    assert result["status"] == "incomplete"
    assert result["pending_criterion_ids"] == ["D1-1"]
