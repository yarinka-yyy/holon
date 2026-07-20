import sys
import uuid

import pytest

from holon_wallet.single_instance import ProcessInstance, WindowsInstanceBackend


class Backend:
    def __init__(self, owned: bool) -> None:
        self.owned = owned
        self.handle = object()
        self.activated: list[str] = []
        self.closed: list[object] = []

    def create(self, name: str) -> tuple[object, bool]:
        assert name == r"Local\HolonWallet.M3.01"
        return self.handle, self.owned

    def activate(self, title: str) -> bool:
        self.activated.append(title)
        return True

    def close(self, handle: object) -> None:
        self.closed.append(handle)


def test_first_process_owns_mutex_until_idempotent_release() -> None:
    backend = Backend(owned=True)
    instance = ProcessInstance(
        r"Local\HolonWallet.M3.01", "Holon Wallet", backend,
    )

    assert instance.acquire()
    assert backend.activated == []
    assert backend.closed == []
    instance.release()
    instance.release()
    assert backend.closed == [backend.handle]


def test_second_process_activates_first_and_does_not_keep_handle() -> None:
    backend = Backend(owned=False)
    instance = ProcessInstance(
        r"Local\HolonWallet.M3.01", "Holon Wallet", backend,
    )

    assert not instance.acquire()
    assert backend.activated == ["Holon Wallet"]
    assert backend.closed == [backend.handle]
    instance.release()
    assert backend.closed == [backend.handle]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex only")
def test_real_windows_named_mutex_has_only_one_owner() -> None:
    backend = WindowsInstanceBackend()
    name = rf"Local\HolonWallet.Test.{uuid.uuid4()}"
    first_handle, first_owned = backend.create(name)
    try:
        second_handle, second_owned = backend.create(name)
        try:
            assert first_owned
            assert not second_owned
        finally:
            backend.close(second_handle)
    finally:
        backend.close(first_handle)
