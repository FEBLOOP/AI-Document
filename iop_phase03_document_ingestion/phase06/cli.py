from __future__ import annotations

import argparse
import json
from pathlib import Path

from .summary import build_summary
from .validation import validate_summary_integrity


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 06: create a Good Example-guided summary from immutable Phase 05 output.")
    parser.add_argument("--phase05", required=True, help="Phase 05 assessment JSON")
    parser.add_argument("--good-examples", required=True, help="Good Examples KB JSON")
    parser.add_argument("--output", required=True, help="Output Phase 06 JSON")
    parser.add_argument("--provider", choices=["none", "huggingface", "ollama", "openai"], default="none", help="LLM provider; use none for deterministic output")
    parser.add_argument("--model", help="Overrides IOP_LLM_MODEL (defaults to Qwen/Qwen3-8B)")
    args = parser.parse_args()
    phase05 = json.loads(Path(args.phase05).read_text(encoding="utf-8"))
    good_examples = json.loads(Path(args.good_examples).read_text(encoding="utf-8"))
    summary = build_summary(phase05, good_examples, args.provider, args.model)
    validate_summary_integrity(summary)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Created Phase 06 summary for {summary['document_id']}: {target}")


if __name__ == "__main__":
    main()
