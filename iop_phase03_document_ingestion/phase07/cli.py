from __future__ import annotations

import argparse
import json
from pathlib import Path

from phase06.validation import validate_summary_integrity

from .report import build_pdf_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 07: render a Phase 06 JSON summary as PDF.")
    parser.add_argument("--phase06", required=True, help="Phase 06 summary JSON")
    parser.add_argument("--output", required=True, help="Output PDF")
    args = parser.parse_args()

    source = Path(args.phase06)
    phase06 = json.loads(source.read_text(encoding="utf-8"))
    validate_summary_integrity(phase06)
    target = build_pdf_report(phase06, args.output)
    print(f"Created PDF report: {target}")


if __name__ == "__main__":
    main()
