"""Pinned production-disabled baseline policy."""

from pathlib import Path

from .loader import load_policy
from .model import Policy

BASELINE_POLICY_PATH = Path(__file__).with_name("baseline-policy.json")
BASELINE_POLICY_DIGEST = "a48bec4155b590d3d36839832e05e9b5c4e92166de689889b52a5784459206a9"


def load_baseline_policy() -> Policy:
    return load_policy(BASELINE_POLICY_PATH, BASELINE_POLICY_DIGEST)
