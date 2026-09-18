from __future__ import annotations
import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import SCHEMA_VERSION
from llm import load_llm_settings

from .assessment import assess_with_llm, review_packet
from .retrieval import evidence_item, retrieve
from .rubric import load_criteria


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 05: retrieve Phase 04 evidence and assess IOP D1-D8.")
    parser.add_argument("--phase04", required=True, help="Phase 04 JSON")
    parser.add_argument("--rubric", required=True, help="IOP reference JSON")
    parser.add_argument("--output", required=True)
    parser.add_argument("--provider", choices=["none", "huggingface", "ollama", "openai"], default="none", help="LLM provider; use none for evidence-only output")
    parser.add_argument("--model", help="Overrides IOP_LLM_MODEL (defaults to Qwen/Qwen3-8B)")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    phase04 = json.loads(Path(args.phase04).read_text(encoding="utf-8"))
    rubric = json.loads(Path(args.rubric).read_text(encoding="utf-8"))
    settings = load_llm_settings(args.provider, args.model) if args.provider != "none" else None
    results = []
    for criterion in load_criteria(rubric):
        query = " ".join([criterion["criterion"], *criterion["assessment_points"], *criterion["supporting_documents"]])
        evidence = [evidence_item(hit) for hit in retrieve(phase04["chunks"], query, args.top_k)]
        packet = review_packet(criterion, evidence)
        assessment = assess_with_llm(packet, settings) if settings else None
        results.append({"criterion": criterion, "evidence": evidence, "assessment": assessment,
                        "status": "assessed" if assessment else "ready_for_human_or_llm_review"})
    by_dimension = defaultdict(list)
    for result in results: by_dimension[result["criterion"]["dimension_id"]].append(result)
    output = {"schema_version": SCHEMA_VERSION, "document_id": phase04["document_id"], "created_at": datetime.now(timezone.utc).isoformat(),
              "assessment_provider": settings.provider if settings else "none", "assessment_model": settings.model if settings else None,
              "dimensions": [{"dimension_id": key, "criteria": value} for key, value in by_dimension.items()]}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Created assessment package for {len(results)} criteria: {args.output}")


if __name__ == "__main__": main()
