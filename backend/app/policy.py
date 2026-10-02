"""Typed access to policy.yaml.

Every threshold, multiplier, reason code and customer-facing string in the
system is resolved through this module. Nothing else is allowed to read the
YAML file, which is what makes "the DB and the policy docs can never disagree"
a testable claim rather than a hope.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

# backend/app/policy.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO_ROOT / "policy.yaml"


class Policy:
    """Thin, typed-ish wrapper over the parsed policy document."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.global_gate: dict[str, Any] = raw["global_gate"]
        self.tiers: dict[str, Any] = raw["tiers"]
        self.scoring: dict[str, Any] = raw["scoring"]
        self.reward_valuation: dict[str, Any] = raw["reward_valuation"]
        self.near_miss: dict[str, Any] = raw["near_miss"]
        self.reasons: dict[str, Any] = raw["reasons"]
        self.near_miss_reason_codes: list[str] = raw.get("near_miss", {}).get(
            "reason_codes", []
        )
        self.required_fields: list[str] = raw["required_profile_fields"]
        self.spend_categories: list[str] = raw["spend_categories"]
        self.spend_shares: dict[str, Any] = raw["spend_shares"]
        self.product: dict[str, Any] = raw["product"]

    # -- gate ----------------------------------------------------------------
    @property
    def min_age(self) -> int:
        return int(self.global_gate["min_age"])

    @property
    def max_age(self) -> int:
        return int(self.global_gate["max_age"])

    @property
    def min_cibil(self) -> int:
        return int(self.global_gate["min_cibil"])

    @property
    def new_to_credit_cibil(self) -> int:
        return int(self.global_gate["new_to_credit_cibil"])

    @property
    def max_missed_payments_12m(self) -> int:
        return int(self.global_gate["max_missed_payments_12m"])

    @property
    def max_recent_inquiries_6m(self) -> int:
        return int(self.global_gate["max_recent_inquiries_6m"])

    @property
    def max_utilization_pct(self) -> float:
        return float(self.global_gate["max_utilization_pct"])

    # -- reward valuation ----------------------------------------------------
    @property
    def point_value_rs(self) -> float:
        return float(self.reward_valuation["point_value_rs"])

    @property
    def default_point_value_rs(self) -> float:
        return float(self.reward_valuation["default_point_value_rs"])

    @property
    def lounge_visit_value_rs(self) -> float:
        return float(self.reward_valuation["lounge_visit_value_rs"])

    @property
    def lounge_counts_if_preferred(self) -> set[str]:
        return set(self.reward_valuation["lounge_counts_if_preferred"])

    @property
    def lounge_annual_credit_cap_rs(self) -> float:
        return float(self.reward_valuation["lounge_annual_credit_cap_rs"])

    # -- scoring -------------------------------------------------------------
    @property
    def scoring_weights(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.scoring["weights"].items()}

    @property
    def min_net_value_rs(self) -> float:
        return float(self.scoring["min_net_value_rs"])

    @property
    def relative_score_floor(self) -> float:
        return float(self.scoring["relative_score_floor"])

    @property
    def max_recommendations(self) -> int:
        return int(self.scoring["max_recommendations"])

    @property
    def count_lounge_only_if_preferred(self) -> bool:
        return bool(self.scoring["count_lounge_only_if_preferred"])

    @property
    def fee_waived_if_spend_meets_threshold(self) -> bool:
        return bool(self.scoring["fee_waived_if_spend_meets_threshold"])

    # -- near miss -----------------------------------------------------------
    @property
    def nm_max_failed_rules(self) -> int:
        return int(self.near_miss["max_failed_rules"])

    @property
    def nm_income_slack_ratio(self) -> float:
        return float(self.near_miss["income_slack_ratio"])

    @property
    def nm_income_slack_absolute(self) -> float:
        return float(self.near_miss["income_slack_absolute"])

    @property
    def nm_cibil_absolute_slack(self) -> int:
        return int(self.near_miss["cibil_absolute_slack"])

    @property
    def nm_age_slack_years(self) -> int:
        return int(self.near_miss["age_slack_years"])

    # -- reasons -------------------------------------------------------------
    def reason(self, code: str) -> dict[str, Any]:
        """Customer-facing wording for a reason code.

        Raises on an unknown code: an unlabelled rejection reason reaching the
        user is a bug, not a cosmetic problem.
        """
        try:
            return self.reasons[code]
        except KeyError as exc:  # pragma: no cover - guards a typo in the engine
            raise KeyError(f"Unknown reason code {code!r}; add it to policy.yaml") from exc

    def message(self, code: str, **kwargs: Any) -> str:
        return str(self.reason(code)["message"]).format(**kwargs)

    def improve(self, code: str, **kwargs: Any) -> str:
        return str(self.reason(code)["improve"]).format(**kwargs)

    def label(self, code: str) -> str:
        return str(self.reason(code)["label"])

    def tier(self, name: str) -> dict[str, Any]:
        return self.tiers[name]

    def tier_order(self) -> list[str]:
        """Tier names ordered from most to least accessible."""
        return sorted(self.tiers, key=lambda t: self.tiers[t]["order"])


@functools.lru_cache(maxsize=1)
def get_policy(path: str | None = None) -> Policy:
    p = Path(path) if path else POLICY_PATH
    with p.open() as fh:
        return Policy(yaml.safe_load(fh))


def reset_policy_cache() -> None:
    """Tests mutate the YAML to prove thresholds are read, not hardcoded."""
    get_policy.cache_clear()
