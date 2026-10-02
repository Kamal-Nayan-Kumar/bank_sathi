#!/usr/bin/env python
"""Run the evaluation and write results.json + results.md.

    python evaluation/run_eval.py              # everything
    python evaluation/run_eval.py --skip-llm  # no API calls, CI mode
    python evaluation/run_eval.py --limit 20   # fewer profiles, faster
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "evaluation"))

from app.bootstrap import ensure_ready  # noqa: E402
from harness import run, to_markdown  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    # The harness runs the whole pipeline, so the corpus must exist first.
    print("preparing corpus...")
    info = ensure_ready()
    print(f"  {info['cards']} cards, {info['chunks']} policy chunks, store={info['store']}")

    print("running evaluation...")
    result = run(args.limit, skip_llm=args.skip_llm)

    out_dir = REPO_ROOT / "evaluation"
    (out_dir / "results.json").write_text(json.dumps(result, indent=2))
    (out_dir / "results.md").write_text(to_markdown(result))

    for section in result["sections"]:
        head = ", ".join(
            f"{k}={v}" for k, v in list(section["metrics"].items())[:3]
            if not isinstance(v, dict)
        )
        print(f"  {section['section']:<28} n={section['n']:<4} {head}")
    print(f"\nwrote {out_dir / 'results.md'}")


if __name__ == "__main__":
    main()
