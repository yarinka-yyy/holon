"""Pinned production-disabled baseline policy."""

from pathlib import Path

from .loader import load_policy
from .model import Policy

BASELINE_POLICY_PATH = Path(__file__).with_name("baseline-policy.json")
INSTALLED_POLICY_RELATIVE_PATH = Path("holon_policy") / "baseline-policy.json"
BASELINE_POLICY_DIGEST = "2e413a71a76e7f38cad27d73247f707912992418fa5e21bf93940e68c32ba369"


def load_baseline_policy(path: Path | None = None) -> Policy:
    return load_policy(path or BASELINE_POLICY_PATH, BASELINE_POLICY_DIGEST)
