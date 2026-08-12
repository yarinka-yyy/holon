from __future__ import annotations

import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from holon_guard.wallet import (
    WALLET_READINESS_TIMEOUT,
    SubprocessWalletController,
    VerifiedWalletController,
)
from holon_guard.__main__ import _wallet_controller
from holon_wallet_control import AUTHORITY_VERSION, ControlProtocolError, ControlUnavailable


class FakeProcess:
    pid = 202

    def __init__(self, exit_code: int | None = None) -> None:
        self.exit_code = exit_code

    def poll(self) -> int | None:
        return self.exit_code


class GuardWalletTests(unittest.TestCase):
    @staticmethod
    def authority_request() -> dict[str, object]:
        return {
            "authority_version": AUTHORITY_VERSION, "kind": "prepare_transfer",
            "flow_id": "11111111-1111-4111-8111-111111111111",
            "action_id": "act-22222222-2222-4222-8222-222222222222",
            "policy_version": "1", "network": "base", "asset": "usdc",
            "policy_revision": 1, "policy_digest": "c" * 64,
            "amount_atomic": "1000000",
            "recipient": "0x4444444444444444444444444444444444444444",
            "created_at": "2026-07-23T12:00:00Z",
            "expires_at": "2026-07-23T12:05:00Z",
        }

    @staticmethod
    def authority_response(request: dict[str, object]) -> dict[str, object]:
        return {
            "authority_version": AUTHORITY_VERSION, "kind": "transfer_prepared",
            "flow_id": request["flow_id"], "action_id": request["action_id"],
            "wallet_pid": 202, "profile_id": "profile-one",
            "sender": "0x2222222222222222222222222222222222222222",
            "recipient": request["recipient"], "network": request["network"],
            "asset": request["asset"], "amount_atomic": request["amount_atomic"],
            "target": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "selector": "0xa9059cbb", "calldata_hash": "b" * 64,
            "policy_revision": request["policy_revision"],
            "policy_digest": request["policy_digest"],
            "max_total_fee_wei": "500", "prepared_digest": "a" * 64,
            "created_at": request["created_at"], "expires_at": request["expires_at"],
            "code": "TRANSFER_PREPARED",
        }
    def test_installed_path_is_derived_and_development_path_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = _wallet_controller(
                Namespace(
                    require_install_integrity=True,
                    app_root=root / "app",
                    wallet_path=root / "untrusted.exe",
                ),
                None,
            )
            development = _wallet_controller(
                Namespace(
                    require_install_integrity=False,
                    app_root=None,
                    wallet_path=root / "dev" / "HolonWallet.exe",
                ),
                None,
            )
            missing = _wallet_controller(
                Namespace(
                    require_install_integrity=False,
                    app_root=None,
                    wallet_path=None,
                ),
                None,
            )
            failed = _wallet_controller(
                Namespace(
                    require_install_integrity=True,
                    app_root=root / "app",
                    wallet_path=None,
                ),
                "WALLET_INTEGRITY_FAILED",
            )
        self.assertEqual(
            installed._wallet_path, (root / "app" / "HolonWallet.exe").resolve(),
        )
        self.assertEqual(
            development._wallet_path,
            (root / "dev" / "HolonWallet.exe").resolve(),
        )
        self.assertFalse(missing.open_public().ok)
        self.assertFalse(failed.open_public().ok)

    def test_fixed_command_uses_shell_false_and_can_activate(self) -> None:
        process = FakeProcess()
        activated: list[int] = []
        closed: list[int] = []
        controller = SubprocessWalletController(
            ("mock-wallet.exe", "--fixture"),
            close_callback=lambda handle: closed.append(handle.pid),
            activate_callback=lambda handle: activated.append(handle.pid),
        )
        with patch("holon_guard.wallet.subprocess.Popen", return_value=process) as popen:
            self.assertIs(controller.open_or_activate("flow-one"), process)
            self.assertIs(controller.open_or_activate("flow-two"), process)
        command = popen.call_args.args[0]
        self.assertEqual(command, ["mock-wallet.exe", "--fixture", "flow-one"])
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(activated, [202])
        controller.request_close(process)
        self.assertEqual(closed, [202])

    def test_mock_fixture_process_reports_normal_and_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "mock_wallet.py"
            fixture.write_text("raise SystemExit(0)\n", encoding="utf-8")
            normal = SubprocessWalletController((sys.executable, str(fixture)))
            normal_handle = normal.open_or_activate("flow-normal")
            normal_code = normal_handle.wait(timeout=5)  # type: ignore[attr-defined]
            fixture.write_text("raise SystemExit(7)\n", encoding="utf-8")
            failed = SubprocessWalletController((sys.executable, str(fixture)))
            failed_handle = failed.open_or_activate("flow-failed")
            failed_code = failed_handle.wait(timeout=5)  # type: ignore[attr-defined]
        self.assertEqual(normal_code, 0)
        self.assertEqual(failed_code, 7)

    def test_verified_controller_activates_existing_wallet_without_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")
            calls: list[tuple[str, Path, float]] = []

            class Control:
                def activate(self, launch_id: str, expected: Path, timeout: float) -> int:
                    calls.append((launch_id, expected, timeout))
                    return 202

            spawned: list[object] = []
            controller = VerifiedWalletController(
                path, Control(), lambda *args, **kwargs: spawned.append((args, kwargs)),
            )
            result = controller.open_public()
        self.assertTrue(result.ok)
        self.assertEqual(result.wallet_state, "ACTIVATED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(spawned, [])

    def test_verified_controller_spawns_once_then_waits_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")
            calls = 0

            class Control:
                def activate(self, launch_id: str, expected: Path, timeout: float) -> int:
                    nonlocal calls
                    del launch_id, expected, timeout
                    calls += 1
                    if calls == 1:
                        raise ControlUnavailable("not ready")
                    return 303

            spawned: list[tuple[object, object]] = []
            controller = VerifiedWalletController(
                path,
                Control(),
                lambda *args, **kwargs: spawned.append((args, kwargs)),
            )
            result = controller.open_public()
        self.assertTrue(result.ok)
        self.assertEqual(result.wallet_state, "OPENED")
        self.assertEqual(calls, 2)
        self.assertEqual(len(spawned), 1)
        command = spawned[0][0][0]
        self.assertEqual(command, [str(path.resolve())])
        self.assertFalse(spawned[0][1]["shell"])

    def test_protocol_mismatch_never_spawns_or_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")
            calls = 0

            class Control:
                def activate(self, launch_id: str, expected: Path, timeout: float) -> int:
                    nonlocal calls
                    del launch_id, expected, timeout
                    calls += 1
                    raise ControlProtocolError("private mismatch detail")

            spawned: list[object] = []
            result = VerifiedWalletController(
                path, Control(), lambda *args, **kwargs: spawned.append(args),
            ).open_public()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "CONTROL_PROTOCOL_FAILED")
        self.assertEqual(calls, 1)
        self.assertEqual(spawned, [])
        self.assertNotIn("mismatch", result.message)

    def test_open_public_distinguishes_missing_and_spawn_failure(self) -> None:
        class Control:
            def activate(self, *_args):
                raise ControlUnavailable("not ready")

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            missing = VerifiedWalletController(path, Control()).open_public()
            path.write_bytes(b"fixture")

            def fail_spawn(*_args, **_kwargs):
                raise OSError("private spawn detail")

            failed = VerifiedWalletController(
                path, Control(), fail_spawn,
            ).open_public()

        self.assertEqual(missing.code, "WALLET_EXECUTABLE_MISSING")
        self.assertEqual(failed.code, "WALLET_START_FAILED")
        self.assertNotIn("private", failed.message.lower())

    def test_open_public_classifies_post_spawn_exit_and_timeout(self) -> None:
        class Control:
            def activate(self, *_args):
                raise ControlUnavailable("not ready")

        cases = [
            (None, "WALLET_STARTUP_TIMEOUT"),
            (0, "WALLET_INSTANCE_UNREACHABLE"),
            (21, "WALLET_INSTANCE_UNREACHABLE"),
            (20, "WALLET_INITIALIZATION_FAILED"),
            (7, "WALLET_EXITED"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")
            for exit_code, expected_code in cases:
                with self.subTest(exit_code=exit_code):
                    process = FakeProcess(exit_code)
                    controller = VerifiedWalletController(
                        path, Control(), lambda *_args, **_kwargs: process,
                    )
                    result = controller.open_public()
                    self.assertEqual(result.code, expected_code)
                    self.assertIs(controller._current, process)
                    self.assertNotIn(str(path), result.message)
                    if expected_code == "WALLET_EXITED":
                        self.assertIn("exit code 7", result.message)

    def test_default_readiness_timeout_is_forty_seconds(self) -> None:
        class Control:
            def __init__(self) -> None:
                self.timeouts: list[float] = []

            def activate(self, _launch_id: str, _expected: Path, timeout: float) -> int:
                self.timeouts.append(timeout)
                if len(self.timeouts) == 1:
                    raise ControlUnavailable("not ready")
                return 202

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")
            control = Control()
            result = VerifiedWalletController(
                path, control, lambda *_args, **_kwargs: FakeProcess(),
            ).open_public()

        self.assertTrue(result.ok)
        self.assertEqual(control.timeouts, [0.15, WALLET_READINESS_TIMEOUT])
        self.assertEqual(WALLET_READINESS_TIMEOUT, 40.0)

    def test_process_verification_code_is_preserved_without_private_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")

            class Control:
                def activate(self, *_args):
                    raise ControlProtocolError(
                        "private pid path pipe detail",
                        "WALLET_PROCESS_VERIFICATION_FAILED",
                    )

            result = VerifiedWalletController(path, Control()).open_public()

        self.assertEqual(result.code, "WALLET_PROCESS_VERIFICATION_FAILED")
        for value in ("private", "pid", "path", "pipe"):
            self.assertNotIn(value, result.message.lower())

    def test_public_balances_spawn_one_hidden_worker_and_read_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")
            reads: list[tuple[str, Path, float, float]] = []

            class PublicControl:
                def read(
                    self, query_id: str, expected: Path,
                    readiness_timeout: float, response_timeout: float,
                ) -> dict[str, object]:
                    reads.append((query_id, expected, readiness_timeout, response_timeout))
                    return {"status": "READY"}

            spawned: list[tuple[object, object]] = []
            controller = VerifiedWalletController(
                path,
                process_factory=lambda *args, **kwargs: spawned.append((args, kwargs)),
                public_control=PublicControl(),  # type: ignore[arg-type]
            )
            result = controller.read_public_balances()
        self.assertTrue(result.ok)
        self.assertEqual(result.payload, {"status": "READY"})
        self.assertEqual(len(spawned), 1)
        self.assertEqual(
            spawned[0][0][0], [str(path.resolve()), "--public-balances-worker"],
        )
        self.assertFalse(spawned[0][1]["shell"])
        self.assertEqual(len(reads), 1)

    def test_public_balance_failure_does_not_retry_or_expose_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")
            calls = 0

            class PublicControl:
                def read(self, *args, **kwargs):
                    nonlocal calls
                    del args, kwargs
                    calls += 1
                    raise ControlProtocolError("sensitive path and query")

            spawned: list[object] = []
            result = VerifiedWalletController(
                path,
                process_factory=lambda *args, **kwargs: spawned.append(args),
                public_control=PublicControl(),  # type: ignore[arg-type]
            ).read_public_balances()
        self.assertFalse(result.ok)
        self.assertIsNone(result.payload)
        self.assertEqual(calls, 1)
        self.assertEqual(len(spawned), 1)

    def test_lending_preview_spawns_one_hidden_worker_without_gui(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")
            calls: list[tuple[dict[str, object], Path, float, float]] = []

            class PreviewControl:
                def prepare(self, request, expected, readiness, response):
                    calls.append((request, expected, readiness, response))
                    return {"preview": {"status": "UNAVAILABLE"}}

            spawned: list[tuple[object, object]] = []
            controller = VerifiedWalletController(
                path,
                process_factory=lambda *args, **kwargs: spawned.append((args, kwargs)),
                lending_preview_control=PreviewControl(),  # type: ignore[arg-type]
            )
            result = controller.preview_lending({"action": "supply"}, "a" * 64)
        self.assertTrue(result.ok)
        self.assertEqual(result.payload, {"status": "UNAVAILABLE"})
        self.assertEqual(len(spawned), 1)
        self.assertEqual(
            spawned[0][0][0], [str(path.resolve()), "--lending-preview-worker"],
        )
        self.assertFalse(spawned[0][1]["shell"])
        self.assertEqual(len(calls), 1)

    def test_authority_existing_wallet_prepares_without_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")
            request = self.authority_request()
            calls = []

            class Authority:
                def exchange(self, *args):
                    calls.append(args)
                    return GuardWalletTests.authority_response(request)

            spawned = []
            result = VerifiedWalletController(
                path, process_factory=lambda *args, **kwargs: spawned.append(args),
                authority_control=Authority(),  # type: ignore[arg-type]
            ).prepare_transfer(request)
        self.assertTrue(result.ok)
        self.assertEqual(result.payload["prepared_digest"], "a" * 64)
        self.assertEqual(len(calls), 1)
        self.assertEqual(spawned, [])

    def test_authority_cold_start_spawns_once_and_never_retries_protocol_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")
            request = self.authority_request()
            calls = 0

            class Authority:
                def exchange(self, *args):
                    nonlocal calls
                    del args
                    calls += 1
                    if calls == 1:
                        raise ControlUnavailable("not ready")
                    return GuardWalletTests.authority_response(request)

            spawned = []
            result = VerifiedWalletController(
                path, process_factory=lambda *args, **kwargs: spawned.append((args, kwargs)),
                authority_control=Authority(),  # type: ignore[arg-type]
            ).prepare_transfer(request)
            self.assertTrue(result.ok)
            self.assertEqual(calls, 2)
            self.assertEqual(len(spawned), 1)

            class Broken:
                def exchange(self, *args):
                    del args
                    raise ControlProtocolError("private mismatch")

            untouched = []
            failed = VerifiedWalletController(
                path, process_factory=lambda *args, **kwargs: untouched.append(args),
                authority_control=Broken(),  # type: ignore[arg-type]
            ).prepare_transfer(request)
        self.assertFalse(failed.ok)
        self.assertEqual(failed.code, "WALLET_PREPARATION_AMBIGUOUS")
        self.assertEqual(failed.payload, {
            "stage": "WALLET_PREPARE", "failure_category": "wallet_ipc",
            "ipc_outcome": "WALLET_RESPONSE_INVALID",
        })
        self.assertEqual(untouched, [])

    def test_prepare_transfer_preserves_post_spawn_startup_timeout(self) -> None:
        request = self.authority_request()

        class Authority:
            def __init__(self) -> None:
                self.calls = 0
                self.timeouts: list[float] = []

            def exchange(
                self, _request: dict[str, object], _expected: Path,
                readiness_timeout: float, _response_timeout: float,
            ) -> dict[str, object]:
                self.calls += 1
                self.timeouts.append(readiness_timeout)
                raise ControlUnavailable("private pipe detail")

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HolonWallet.exe"
            path.write_bytes(b"fixture")
            authority = Authority()
            spawned: list[FakeProcess] = []
            result = VerifiedWalletController(
                path,
                process_factory=lambda *_args, **_kwargs: spawned.append(FakeProcess()) or spawned[-1],
                authority_control=authority,  # type: ignore[arg-type]
            ).prepare_transfer(request)

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "WALLET_STARTUP_TIMEOUT")
        self.assertIsNone(result.payload)
        self.assertEqual(authority.calls, 2)
        self.assertEqual(authority.timeouts, [0.15, WALLET_READINESS_TIMEOUT])
        self.assertEqual(len(spawned), 1)


if __name__ == "__main__":
    unittest.main()
