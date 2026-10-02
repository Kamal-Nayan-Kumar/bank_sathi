#!/usr/bin/env python
"""Build every generated artefact from the seed.

    python scripts/build_data.py            # cards, policies, profiles, ingest
    python scripts/build_data.py --profiles 200

Everything here is deterministic. Running it twice produces identical files,
which is what makes the evaluation numbers in the README reproducible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.bootstrap import ensure_ready  # noqa: E402
from app.catalogue import generate_cards  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_all_cards, init_db, upsert_cards  # noqa: E402
from app.survey import (  # noqa: E402
    edge_case_profiles,
    generate_profiles,
    ground_truth,
    write_profiles,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", type=int, default=120)
    ap.add_argument("--profiles", type=int, default=300)
    ap.add_argument("--skip-ingest", action="store_true")
    ap.add_argument(
        "--force",
        action="store_true",
        help="rebuild the catalogue even if the database already has rows",
    )
    args = ap.parse_args()

    settings = get_settings()
    data = REPO_ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)

    # --- cards -------------------------------------------------------------
    cards = generate_cards(args.cards, settings.synthetic_seed)
    cards_path = data / "cards.json"
    cards_path.write_text(
        json.dumps([c.model_dump(mode="json") for c in cards], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"cards        : {len(cards)} -> {cards_path.relative_to(REPO_ROOT)}")

    # --- policy documents --------------------------------------------------
    docs = sorted(settings.resolve(settings.policy_docs_dir).glob("*.md"))
    print(f"policy docs  : {len(docs)} hand-written markdown files (no generation)")

    # --- profiles + ground truth ------------------------------------------
    profiles = generate_profiles(args.profiles, settings.synthetic_seed, cards)
    edges = edge_case_profiles(cards)
    all_profiles = profiles + edges

    init_db()
    live_cards = get_all_cards()
    if live_cards and not args.force and len(live_cards) != len(cards):
        # The catalogue in the database came from a different --cards value.
        # Carry on, but say so: a silent mismatch makes evaluation numbers
        # impossible to reproduce.
        print(
            f"note        : database holds {len(live_cards)} cards, generating "
            f"{len(cards)}. Ground truth will use the database's set. "
            f"Pass --force to overwrite."
        )
    upsert_cards(
        cards,
        {c.card_id: [r.model_dump(mode="json") for r in c.reward_rules] for c in cards},
    )
    live_cards = get_all_cards()

    truth = [ground_truth(p, live_cards) for p in all_profiles]
    profiles_path = data / "profiles.jsonl"
    truth_path = data / "ground_truth.json"
    write_profiles(all_profiles, profiles_path)
    truth_path.write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print(
        f"profiles     : {len(profiles)} generated + {len(edges)} edge cases "
        f"-> {profiles_path.relative_to(REPO_ROOT)}"
    )
    print(f"ground truth : {len(truth)} -> {truth_path.relative_to(REPO_ROOT)}")

    # --- corpus ------------------------------------------------------------
    if args.skip_ingest:
        print("ingest       : skipped")
        return
    info = ensure_ready(args.cards)
    print(
        f"ingest       : {info['chunks']} chunks from {info['policy_docs']} docs "
        f"into {info['store']}"
    )

    # --- distribution ------------------------------------------------------
    buckets = {"gate_rejected": 0, "no_eligible": 0, "recommended": 0}
    for t in truth:
        if not t["gate_passed"]:
            buckets["gate_rejected"] += 1
        elif not t["eligible_card_ids"]:
            buckets["no_eligible"] += 1
        else:
            buckets["recommended"] += 1
    print(f"outcomes     : {buckets}")


if __name__ == "__main__":
    main()
