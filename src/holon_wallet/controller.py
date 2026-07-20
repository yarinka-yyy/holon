"""Small QObject bridge between the in-memory model and QML."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from .model import ProfileSummary, WalletShellState

PROFILE_INITIALS = {"main": "A1", "trading": "T1", "savings": "S2"}


def _profile_map(profile: ProfileSummary) -> dict[str, object]:
    return {
        "id": profile.profile_id,
        "label": profile.label,
        "address": profile.address,
        "shortAddress": profile.short_address,
        "simulated": profile.simulated,
        "initials": PROFILE_INITIALS.get(
            profile.profile_id,
            "".join(word[0] for word in profile.label.split())[:2],
        ),
    }


class WalletController(QObject):
    """Exposes only prototype navigation and public profile summaries."""

    activeProfileChanged = Signal()
    currentScreenChanged = Signal()

    def __init__(self, state: WalletShellState | None = None) -> None:
        super().__init__()
        self._state = state or WalletShellState()
        self._current_screen = "main"

    @Property("QVariantList", constant=True)
    def profiles(self) -> list[dict[str, object]]:
        return [_profile_map(profile) for profile in self._state.profiles]

    @Property("QVariantMap", notify=activeProfileChanged)
    def activeProfile(self) -> dict[str, object]:
        return _profile_map(self._state.active_profile)

    @Property(str, notify=activeProfileChanged)
    def activeProfileId(self) -> str:
        return self._state.active_profile_id

    @Property(str, notify=currentScreenChanged)
    def currentScreen(self) -> str:
        return self._current_screen

    @Slot(str, result=bool)
    def selectProfile(self, profile_id: str) -> bool:
        previous = self._state.active_profile_id
        if not self._state.select_profile(profile_id):
            return False
        if previous != self._state.active_profile_id:
            self.activeProfileChanged.emit()
        return True

    @Slot()
    def showMain(self) -> None:
        self._set_screen("main")

    @Slot()
    def showWallets(self) -> None:
        self._set_screen("wallets")

    def _set_screen(self, screen: str) -> None:
        if screen == self._current_screen:
            return
        self._current_screen = screen
        self.currentScreenChanged.emit()
