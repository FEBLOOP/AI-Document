from __future__ import annotations

import argparse
import json
from pathlib import Path

from phase06.validation import validate_summary_integrity

from .report import build_report
from .scoring import calculate_scoring


DEFAULT_RUBRIC = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "knowledge_base"
    / "iop_reference.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 07: render a Phase 06 JSON summary as PDF or DOCX.")
    parser.add_argument("--phase06", required=True, help="Phase 06 summary JSON")
    parser.add_argument("--output", required=True, help="Output report (.pdf or .docx)")
    parser.add_argument(
        "--rubric",
        default=str(DEFAULT_RUBRIC),
        help="IOP rubric used to verify all criterion IDs (defaults to the project knowledge base)",
    )
    parser.add_argument("--scoring-output", help="Optional Phase 07 scoring JSON output")
    args = parser.parse_args()

    source = Path(args.phase06)
    phase06 = json.loads(source.read_text(encoding="utf-8"))
    validate_summary_integrity(phase06)
    rubric_path = Path(args.rubric)
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    scoring = calculate_scoring(phase06, rubric)
    if args.scoring_output:
        scoring_target = Path(args.scoring_output)
        scoring_target.parent.mkdir(parents=True, exist_ok=True)
        scoring_target.write_text(
            json.dumps(
                {
                    "schema_version": "ai-document.phase07-scoring/v1",
                    "document_id": phase06.get("document_id"),
                    "scoring": scoring,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Created Phase 07 scoring JSON: {scoring_target}")
    target = build_report(phase06, args.output, rubric)
    print(f"Created {target.suffix[1:].upper()} report: {target}")
    if scoring["status"] != "complete":
        print(
            f"Scoring is incomplete: {scoring['scored_criteria']}/{scoring['total_criteria']} "
            "criteria have valid assessed scores."
        )


if __name__ == "__main__":
    main()
