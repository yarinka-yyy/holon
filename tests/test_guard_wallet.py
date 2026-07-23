from __future__ import annotations

import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from holon_guard.wallet import SubprocessWalletController, VerifiedWalletController
from holon_guard.__main__ import _wallet_controller
from holon_wallet_control import ControlProtocolError, ControlUnavailable


class FakeProcess:
    pid = 202

    def poll(self) -> int | None:
        return None


class GuardWalletTests(unittest.TestCase):
    @staticmethod
    def authority_request() -> dict[str, object]:
        return {
            "authority_version": "1", "kind": "prepare_transfer",
            "flow_id": "11111111-1111-4111-8111-111111111111",
            "action_id": "act-22222222-2222-4222-8222-222222222222",
            "policy_version": "1", "network": "base", "asset": "usdc",
            "amount_atomic": "1000000",
            "recipient": "0x4444444444444444444444444444444444444444",
            "created_at": "2026-07-23T12:00:00Z",
            "expires_at": "2026-07-23T12:05:00Z",
        }

    @staticmethod
    def authority_response(request: dict[str, object]) -> dict[str, object]:
        return {
            "authority_version": "1", "kind": "transfer_prepared",
            "flow_id": request["flow_id"], "action_id": request["action_id"],
            "wallet_pid": 202, "profile_id": "profile-one",
            "sender": "0x2222222222222222222222222222222222222222",
            "recipient": request["recipient"], "network": request["network"],
            "asset": request["asset"], "amount_atomic": request["amount_atomic"],
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
        self.assertEqual(result.code, "WALLET_UNAVAILABLE")
        self.assertEqual(calls, 1)
        self.assertEqual(spawned, [])
        self.assertNotIn("mismatch", result.message)

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
        self.assertEqual(untouched, [])


if __name__ == "__main__":
    unittest.main()
