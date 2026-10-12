from __future__ import annotations
import argparse
import json
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from llm import load_llm_settings

from .assessment import assess_dimension_with_llm, review_packet
from .retrieval import evidence_item, retrieve
from .rubric import load_criteria


def _build_output(
    phase04: dict[str, Any],
    settings: Any,
    results: list[dict[str, Any]],
    created_at: str,
    total_criteria: int,
    run_config: dict[str, Any],
) -> dict[str, Any]:
    by_dimension = defaultdict(list)
    for result in results:
        by_dimension[result["criterion"]["dimension_id"]].append(result)
    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": phase04["document_id"],
        "created_at": created_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "complete": len(results) == total_criteria,
        "assessment_provider": settings.provider if settings else "none",
        "assessment_model": settings.model if settings else None,
        "run_config": run_config,
        "dimensions": [
            {"dimension_id": key, "criteria": value}
            for key, value in by_dimension.items()
        ],
    }


def _write_output_atomic(path: Path, output: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _resume_items(
    path: Path,
    phase04: dict[str, Any],
    settings: Any,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    if not path.is_file():
        return {}, None
    previous = json.loads(path.read_text(encoding="utf-8"))
    expected_provider = settings.provider if settings else "none"
    expected_model = settings.model if settings else None
    if previous.get("document_id") != phase04["document_id"]:
        raise ValueError("Resume output belongs to a different document_id.")
    if previous.get("assessment_provider") != expected_provider:
        raise ValueError("Resume output uses a different LLM provider.")
    if previous.get("assessment_model") != expected_model:
        raise ValueError("Resume output uses a different LLM model.")

    completed = {}
    for dimension in previous.get("dimensions", []):
        for item in dimension.get("criteria", []):
            criterion_id = str(item.get("criterion", {}).get("criterion_id", ""))
            if (
                criterion_id
                and item.get("status") == "assessed"
                and isinstance(item.get("assessment"), dict)
            ):
                completed[criterion_id] = item
    return completed, previous.get("created_at")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 05: retrieve Phase 04 evidence and assess IOP D1-D8.")
    parser.add_argument("--phase04", required=True, help="Phase 04 JSON")
    parser.add_argument("--rubric", required=True, help="IOP reference JSON")
    parser.add_argument("--output", required=True)
    parser.add_argument("--provider", choices=["none", "huggingface", "ollama", "openai"], default="none", help="LLM provider; use none for evidence-only output")
    parser.add_argument("--model", help="Overrides IOP_LLM_MODEL (defaults to Qwen/Qwen3-8B)")
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=2, help="Criteria per LLM request")
    parser.add_argument("--max-evidence-chars", type=int, default=800, help="Maximum prompt characters per evidence item")
    parser.add_argument("--retries", type=int, default=2, help="Retries for invalid or incomplete LLM JSON")
    parser.add_argument("--resume", action="store_true", help="Continue from assessed criteria in an existing output file")
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be at least 1")
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if args.max_evidence_chars < 1:
        parser.error("--max-evidence-chars must be at least 1")
    if args.retries < 0:
        parser.error("--retries must be zero or greater")

    phase04 = json.loads(Path(args.phase04).read_text(encoding="utf-8"))
    rubric = json.loads(Path(args.rubric).read_text(encoding="utf-8"))
    settings = load_llm_settings(args.provider, args.model) if args.provider != "none" else None
    output_path = Path(args.output)
    criteria = load_criteria(rubric)
    grouped_criteria = defaultdict(list)
    for criterion in criteria:
        grouped_criteria[criterion["dimension_id"]].append(criterion)

    completed_items: dict[str, dict[str, Any]] = {}
    resumed_created_at = None
    if args.resume:
        completed_items, resumed_created_at = _resume_items(
            output_path,
            phase04,
            settings,
        )
        if completed_items:
            print(
                f"Resuming with {len(completed_items)} assessed criteria already saved.",
                flush=True,
            )

    created_at = resumed_created_at or datetime.now(timezone.utc).isoformat()
    run_config = {
        "top_k": args.top_k,
        "batch_size": args.batch_size,
        "max_evidence_chars": args.max_evidence_chars,
        "retries": args.retries,
    }
    results = []
    total_batches = sum(
        math.ceil(len(dimension_criteria) / args.batch_size)
        for dimension_criteria in grouped_criteria.values()
    )
    batch_index = 0
    for dimension_id, dimension_criteria in grouped_criteria.items():
        dimension_batches = math.ceil(len(dimension_criteria) / args.batch_size)
        for dimension_batch_index, start in enumerate(
            range(0, len(dimension_criteria), args.batch_size),
            start=1,
        ):
            batch_index += 1
            batch_criteria = dimension_criteria[start:start + args.batch_size]
            label = f"{dimension_id} batch {dimension_batch_index}/{dimension_batches}"
            started_at = time.perf_counter()
            print(
                f"[{batch_index}/{total_batches}] Preparing {label} "
                f"({len(batch_criteria)} criteria)...",
                flush=True,
            )

            batch_items = []
            pending_positions = []
            packets = []
            for criterion in batch_criteria:
                criterion_id = str(criterion["criterion_id"])
                if criterion_id in completed_items:
                    batch_items.append(completed_items[criterion_id])
                    continue

                query = " ".join(
                    [
                        criterion["criterion"],
                        *criterion["assessment_points"],
                        *criterion["supporting_documents"],
                    ]
                )
                evidence = [
                    evidence_item(hit)
                    for hit in retrieve(phase04["chunks"], query, args.top_k)
                ]
                packet = review_packet(
                    criterion,
                    evidence,
                    max_evidence_chars=args.max_evidence_chars,
                )
                pending_positions.append(len(batch_items))
                batch_items.append({"criterion": criterion, "evidence": evidence})
                packets.append(packet)

            if settings and packets:
                print(
                    f"[{batch_index}/{total_batches}] Calling LLM for {label}...",
                    flush=True,
                )

                def report_retry(attempt: int, error: Exception) -> None:
                    print(
                        f"[{batch_index}/{total_batches}] Retrying {label} "
                        f"(attempt {attempt}/{args.retries + 1}): {error}",
                        flush=True,
                    )

                assessments = assess_dimension_with_llm(
                    packets,
                    settings,
                    max_attempts=args.retries + 1,
                    on_retry=report_retry,
                )
                for position, assessment in zip(pending_positions, assessments):
                    batch_items[position]["assessment"] = assessment
                    batch_items[position]["status"] = "assessed"
            elif not settings:
                for position in pending_positions:
                    batch_items[position]["assessment"] = None
                    batch_items[position]["status"] = "ready_for_human_or_llm_review"

            results.extend(batch_items)
            output = _build_output(
                phase04,
                settings,
                results,
                created_at,
                len(criteria),
                run_config,
            )
            _write_output_atomic(output_path, output)
            elapsed = time.perf_counter() - started_at
            action = "Skipped" if not pending_positions else "Completed"
            print(
                f"[{batch_index}/{total_batches}] {action} {label} in {elapsed:.1f}s.",
                flush=True,
            )

    print(f"Created assessment package for {len(results)} criteria: {args.output}")


if __name__ == "__main__": main()
