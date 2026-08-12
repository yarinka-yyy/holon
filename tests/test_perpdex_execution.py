from __future__ import annotations

import ast
from pathlib import Path
import secrets
import sys
import uuid

ROOT = Path(__file__).parents[1]
PERPDEX_SRC = ROOT / "modules" / "perpdex" / "src"
sys.path.insert(0, str(PERPDEX_SRC))

from holon_perpdex.guard import GuardProtectedActionAdapter  # noqa: E402
from holon_perpdex.reader import HyperliquidReader, ReaderError  # noqa: E402
from holon_perpdex.wallet import WalletProtectedActionAdapter  # noqa: E402
from holon_wallet.perpdex_action import (  # noqa: E402
    ExchangeOutcomeUnknown, PerpDexExecutor,
)
from holon_wallet.storage import WalletPaths  # noqa: E402
from holon_wallet.vault import VaultRepository  # noqa: E402
from holon_wallet.wallet_crypto import import_private_key  # noqa: E402
from hyperliquid.utils.signing import recover_agent_or_user_from_l1_action  # noqa: E402
from test_perpdex_actions import ActionInfo, CLOCK  # noqa: E402


def test_wallet_executor_has_no_static_hyperliquid_sdk_import() -> None:
    source = ROOT.joinpath("src", "holon_wallet", "perpdex_action.py").read_text(
        encoding="utf-8",
    )
    imports = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    assert all(name != "hyperliquid" and not name.startswith("hyperliquid.") for name in imports)


class SubmitFixture:
    def __init__(self, fill: str | None = None, *, unknown: bool = False) -> None:
        self.fill = fill
        self.unknown = unknown
        self.calls: list[dict[str, object]] = []

    def __call__(self, payload):
        self.calls.append(dict(payload))
        if self.unknown:
            raise ExchangeOutcomeUnknown("unknown")
        if payload["action"]["type"] == "order":
            size = self.fill or payload["action"]["orders"][0]["s"]
            return {
                "status": "ok",
                "response": {"type": "order", "data": {"statuses": [{
                    "filled": {"avgPx": "60000", "oid": 77, "totalSz": size},
                }]}},
            }
        return {"status": "ok", "response": {"type": "default"}}


def fixture(tmp_path: Path):
    password = secrets.token_urlsafe(18)
    repository = VaultRepository(WalletPaths(tmp_path / "wallet"))
    record = repository.new_record(import_private_key("11" * 32), "Main Account")
    repository.create_new(password, record)
    account = {"address": record.summary.address, "label": record.summary.label}
    info = ActionInfo(referred_by={"code": "EXISTING"})
    reader = HyperliquidReader(info)
    guard = GuardProtectedActionAdapter(reader, clock=lambda: CLOCK)
    guard.configure(repository.paths.data_dir)
    params = {
        "leverage": 2, "margin_mode": "ISOLATED", "market": "BTC",
        "notional_usdc": "100", "side": "LONG",
    }
    preview = guard.preview("OPEN_POSITION", params, account)
    bundle = guard.prepare(
        "act-" + str(uuid.uuid4()), "OPEN_POSITION", params, account,
        str(preview.preview_digest),
    )
    wallet = WalletProtectedActionAdapter(reader, clock=lambda: CLOCK)
    wallet.configure(repository.paths.data_dir)
    wallet.mark_operation(bundle.operation_id, "AWAITING_LOCAL_CONFIRMATION")
    return repository, record, password, account, bundle, wallet


def test_executor_signs_each_phase_once_and_persists_no_signature(tmp_path: Path) -> None:
    repository, record, password, account, bundle, wallet = fixture(tmp_path)
    verified_phases: list[int] = []
    verify_phase = wallet.verify_phase

    def tracked_verify_phase(current_bundle, phase_index, current_account):
        verified_phases.append(phase_index)
        return verify_phase(current_bundle, phase_index, current_account)

    wallet.verify_phase = tracked_verify_phase  # type: ignore[method-assign]
    transport = SubmitFixture()
    result = PerpDexExecutor(repository, wallet, transport).execute(
        bundle.to_mapping(), password, record.summary.profile_id, account,
    )
    assert result.status == "COMPLETED"
    assert result.code == "PERPDEX_ACTION_COMPLETED"
    assert len(transport.calls) == 2
    assert verified_phases == [1]
    assert [call["action"]["type"] for call in transport.calls] == [
        "updateLeverage", "order",
    ]
    assert len({call["nonce"] for call in transport.calls}) == 2
    assert all(call["expiresAfter"] is not None for call in transport.calls)
    assert all(set(call["signature"]) == {"r", "s", "v"} for call in transport.calls)
    assert all(
        recover_agent_or_user_from_l1_action(
            call["action"], call["signature"], None, call["nonce"],
            call["expiresAfter"], True,
        ).lower() == account["address"].lower()
        for call in transport.calls
    )
    raw = (repository.paths.data_dir / "perpdex-operations.json").read_text(encoding="utf-8")
    assert all(token not in raw.casefold() for token in (
        "password", "private_key", "signature", "signed_payload", '"action"',
    ))
    stored = wallet.status(bundle.operation_id)
    assert stored is not None and stored["state"] == "COMPLETED"
    assert [phase["state"] for phase in stored["phases"]] == ["CONFIRMED", "CONFIRMED"]


def test_partial_ioc_stops_without_retry_and_preserves_partial_result(tmp_path: Path) -> None:
    repository, record, password, account, bundle, wallet = fixture(tmp_path)
    requested = bundle.phases[-1].semantic["size_asset"]
    partial = str((__import__("decimal").Decimal(requested) / 2).normalize())
    transport = SubmitFixture(fill=partial)
    result = PerpDexExecutor(repository, wallet, transport).execute(
        bundle.to_mapping(), password, record.summary.profile_id, account,
    )
    assert result.status == "PARTIAL"
    assert result.code == "IOC_PARTIAL_FILL"
    assert len(transport.calls) == 2
    assert result.phase_states[-1]["state"] == "PARTIAL"
    assert wallet.status(bundle.operation_id)["state"] == "PARTIAL"


def test_unknown_submit_stops_later_phases_and_wrong_password_submits_nothing(tmp_path: Path) -> None:
    repository, record, password, account, bundle, wallet = fixture(tmp_path)
    unknown = SubmitFixture(unknown=True)
    result = PerpDexExecutor(repository, wallet, unknown).execute(
        bundle.to_mapping(), password, record.summary.profile_id, account,
    )
    assert result.status == "UNKNOWN"
    assert len(unknown.calls) == 1
    assert wallet.status(bundle.operation_id)["phases"][1]["state"] == "PENDING"

    second_root = tmp_path / "wrong"
    repository2, record2, password2, account2, bundle2, wallet2 = fixture(second_root)
    transport = SubmitFixture()
    failed = PerpDexExecutor(repository2, wallet2, transport).execute(
        bundle2.to_mapping(), password2 + "-wrong", record2.summary.profile_id, account2,
    )
    assert failed.status == "FAILED"
    assert failed.code == "AUTHENTICATION_FAILED"
    assert transport.calls == []


def test_price_move_before_auth_persists_safe_terminal_details_and_submits_nothing(
    tmp_path: Path,
) -> None:
    repository, record, password, account, bundle, wallet = fixture(tmp_path)
    wallet.reader._post.prices["BTC"] = ("60999", "61001")
    transport = SubmitFixture()

    result = PerpDexExecutor(repository, wallet, transport).execute(
        bundle.to_mapping(), password, record.summary.profile_id, account,
    )

    assert result.status == "FAILED"
    assert result.code == "PERPDEX_PRICE_MOVED"
    assert result.terminal_stage == "WALLET_EXECUTION_PRE_VERIFY"
    assert result.external_submission_started is False
    assert transport.calls == []
    stored = wallet.status(bundle.operation_id)
    assert stored["terminal_code"] == "PERPDEX_PRICE_MOVED"
    assert stored["terminal_stage"] == "WALLET_EXECUTION_PRE_VERIFY"
    assert stored["failure_category"] == "perpdex_state"
    assert stored["external_submission_started"] is False


def test_persistence_failure_after_submit_is_unknown_not_failed(tmp_path: Path) -> None:
    repository, record, password, account, bundle, wallet = fixture(tmp_path)
    original = wallet.mark_phase

    def fail_confirm(operation_id, phase_id, state, **kwargs):
        if state == "CONFIRMED":
            raise OSError("disk")
        return original(operation_id, phase_id, state, **kwargs)

    wallet.mark_phase = fail_confirm  # type: ignore[method-assign]
    transport = SubmitFixture()
    result = PerpDexExecutor(repository, wallet, transport).execute(
        bundle.to_mapping(), password, record.summary.profile_id, account,
    )
    assert result.status == "UNKNOWN"
    assert result.code == "PERPDEX_RESULT_UNKNOWN"
    assert len(transport.calls) == 1


def test_inter_phase_public_failure_keeps_safe_operation_class_and_stops(
    tmp_path: Path,
) -> None:
    repository, record, password, account, bundle, wallet = fixture(tmp_path)
    original = wallet.reader._post
    books = 0

    def fail_second_book(payload):
        nonlocal books
        if payload.get("type") == "l2Book":
            books += 1
            if books == 2:
                raise ReaderError(
                    "HYPERLIQUID_UNAVAILABLE", "unavailable",
                    operation_class="l2Book",
                )
        return original(payload)

    wallet.reader._post = fail_second_book
    transport = SubmitFixture()
    result = PerpDexExecutor(repository, wallet, transport).execute(
        bundle.to_mapping(), password, record.summary.profile_id, account,
    )

    assert result.status == "FAILED"
    assert result.code == "HYPERLIQUID_UNAVAILABLE"
    assert result.operation_class == "l2Book"
    assert result.external_submission_started is True
    assert [call["action"]["type"] for call in transport.calls] == ["updateLeverage"]
    stored = wallet.status(bundle.operation_id)
    assert stored["operation_class"] == "l2Book"
    assert stored["failure_category"] == "public_transport"
    assert stored["phases"][1]["state"] == "FAILED"
