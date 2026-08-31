from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .pipeline import build_phase04


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Phase 04 retrieval-ready chunks from Phase 03 JSON.")
    parser.add_argument("--input", required=True); parser.add_argument("--output", required=True)
    parser.add_argument("--jsonl", help="Optional one-chunk-per-line output")
    parser.add_argument("--config", help="Optional JSON config file")
    parser.add_argument("--embed", action="store_true", help="Generate embeddings using configured provider")
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    output = build_phase04(data, Settings.load(args.config), args.embed)
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.jsonl:
        Path(args.jsonl).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in output["chunks"]), encoding="utf-8")
    print(f"Created {len(output['chunks'])} chunks: {args.output}")


if __name__ == "__main__": main()
