#!/usr/bin/env python
"""One-shot local runner: build the corpus, run the pipeline, print the answer.

Kept out of the package on purpose. This is the command I run after every
change, and it should stay three lines to invoke.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.bootstrap import ensure_ready  # noqa: E402
from app.graph.run import run_chat, run_profile  # noqa: E402
from app.rag.store import InMemoryStore, set_store  # noqa: E402
from app.schemas import ProfileRequest  # noqa: E402
from tests.conftest import make_profile  # noqa: E402


def bootstrap(count: int = 120) -> list:
    set_store(InMemoryStore())
    return ensure_ready(count)


def show(response) -> None:
    print(f"\n{'=' * 70}")
    print(f"status   : {response.status}  ({response.decision})")
    if response.verifier:
        v = response.verifier
        fails = [c for c in v.checks if not c["passed"]]
        print(f"verifier : {'PASS' if v.passed else 'FAIL'} "
              f"({len(v.checks) - len(fails)}/{len(v.checks)} checks, retries={v.retries}"
              f"{', template fallback' if v.used_fallback else ''})")
        for c in fails:
            print(f"           ! {c['name']}: {c['detail']}")
    if response.question:
        print(f"question : {response.question}")
    print(f"summary  : {response.summary}")
    if response.recommendations:
        print(f"\nrecommended ({len(response.recommendations)}):")
        for r in response.recommendations:
            print(f"  {r.rank}. {r.name} [{r.card_id}] {r.bank} / {r.tier}")
            print(f"     net Rs {r.net_annual_value_rs:,}/yr | fee Rs {r.fee_payable_rs:,} "
                  f"| limit ~Rs {r.est_credit_limit_rs:,} | score {r.score}")
    if response.rejected_cards:
        print("\nblocked:")
        for r in response.rejected_cards:
            print(f"  ! [{r.code}] {r.message}")
    if response.rejections:
        print("\nnot eligible for (sample):")
        for ev in response.rejections[:3]:
            for r in ev.reasons:
                print(f"  x {ev.card_id} [{r.code}] {r.message}")
    if response.near_miss:
        print("\nnear miss:")
        for ev in response.near_miss:
            print(f"  ~ {ev.card_id}: " + "; ".join(r.message for r in ev.reasons))
    if response.improvement_steps:
        print("\nwhat would help:")
        for s in response.improvement_steps:
            print(f"  - {s}")
    print(f"\nevidence: {len(response.evidence)} chunks")
    for e in response.evidence[:3]:
        print(f"  [{e.source}/{e.section}] {e.text[:70]}...")
    print(f"trace   : {json.dumps(response.trace)}")


def main() -> None:
    info = bootstrap()
    print(f"bootstrapped: {info}")
    from app.schemas import NaturalLanguageRequest

    print("\n### FORM ROUTE")
    show(run_profile(ProfileRequest(profile=make_profile())))

    print("\n\n### CHAT ROUTE")
    show(
        run_chat(
            NaturalLanguageRequest(
                message=(
                    "I'm 32, salaried in Bangalore, earning 95k a month with a 775 credit score. "
                    "I travel about 16k a month and eat out a lot. I want lounge access."
                ),
                session_id="demo",
            )
        )
    )

    print("\n\n### REJECTED PROFILE")
    show(
        run_profile(
            ProfileRequest(profile=make_profile(cibil_score=520, missed_payments_12m=2))
        )
    )


if __name__ == "__main__":
    main()
