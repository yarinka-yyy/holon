"""Pinned production-disabled baseline policy."""

from pathlib import Path

from .loader import load_policy
from .model import Policy

BASELINE_POLICY_PATH = Path(__file__).with_name("baseline-policy.json")
INSTALLED_POLICY_RELATIVE_PATH = Path("holon_policy") / "baseline-policy.json"
BASELINE_POLICY_DIGEST = "0cf8678b49b19b06a30c06348327a89eed0b19a59e876d1b2cf5fca16aa126b3"


def load_baseline_policy(path: Path | None = None) -> Policy:
    return load_policy(path or BASELINE_POLICY_PATH, BASELINE_POLICY_DIGEST)
