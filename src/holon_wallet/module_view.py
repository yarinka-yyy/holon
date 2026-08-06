"""Schema-bound data and action surface for the single optional Wallet page."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot

from holon_contracts import MessageKind, make_envelope, new_action_id
from holon_modules import CapabilityRegistry, ModuleLifecycleState


@dataclass(frozen=True, slots=True)
class StaticModuleViewModel:
    module_id: str
    title: str
    body: str

    def to_mapping(self) -> dict[str, str]:
        return {"body": self.body, "moduleId": self.module_id, "title": self.title}


class ModuleViewModel(QObject):
    """Only the reviewed read/action vocabulary exposed to optional QML."""

    changed = Signal()
    _readReady = Signal(int, object)
    _previewReady = Signal(int, object)
    _executeReady = Signal(int, object)

    def __init__(
        self, module_id: str, title: str, body: str, *, reader,
        reader_capability_id: str, action_capability_id: str,
        action_adapter, account_provider: Callable[[], Mapping[str, str] | None],
        action_client, executor: Executor,
        before_execute: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._module_id = module_id
        self._title = title
        self._body = body
        self._reader = reader
        self._reader_capability_id = reader_capability_id
        self._action_capability_id = action_capability_id
        self._action_adapter = action_adapter
        self._account_provider = account_provider
        self._action_client = action_client
        self._executor = executor
        self._before_execute = before_execute
        self._busy = False
        self._message = "Ready"
        self._markets: list[object] = []
        self._portfolio: dict[str, object] = {}
        self._hlp: dict[str, object] = {}
        self._fees: dict[str, object] = {}
        self._history: list[object] = []
        self._prepared: dict[str, object] = {}
        self._prepared_internal: dict[str, object] | None = None
        self._generation = 0
        self._readReady.connect(self._accept_read)
        self._previewReady.connect(self._accept_preview)
        self._executeReady.connect(self._accept_execute)

    @Property(str, constant=True)
    def moduleId(self) -> str:
        return self._module_id

    @Property(str, constant=True)
    def title(self) -> str:
        return self._title

    @Property(str, constant=True)
    def body(self) -> str:
        return self._body

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def statusMessage(self) -> str:
        return self._message

    @Property("QVariantList", notify=changed)
    def markets(self) -> list[object]:
        return list(self._markets)

    @Property("QVariantMap", notify=changed)
    def portfolio(self) -> dict[str, object]:
        return dict(self._portfolio)

    @Property("QVariantMap", notify=changed)
    def hlp(self) -> dict[str, object]:
        return dict(self._hlp)

    @Property("QVariantMap", notify=changed)
    def fees(self) -> dict[str, object]:
        return dict(self._fees)

    @Property("QVariantList", notify=changed)
    def operationHistory(self) -> list[object]:
        return list(self._history)

    @Property("QVariantMap", notify=changed)
    def prepared(self) -> dict[str, object]:
        return dict(self._prepared)

    def _account(self) -> dict[str, str] | None:
        value = self._account_provider()
        if (
            not isinstance(value, Mapping)
            or set(value) != {"address", "label"}
            or not all(isinstance(value[field], str) for field in value)
        ):
            return None
        return {"address": str(value["address"]), "label": str(value["label"])}

    @staticmethod
    def _validated_result(
        module_id: str, capability_id: str, operation: str,
        result: Mapping[str, object],
    ) -> dict[str, object]:
        return dict(make_envelope(MessageKind.MODULE_READ_RESPONSE, {
            "status": "READY", "module_id": module_id,
            "capability_id": capability_id, "operation": operation,
            "result": dict(result), "code": "MODULE_READ_READY",
            "message": "Optional module read completed.",
        }).payload["result"])

    def _read_bundle(self, account: dict[str, str]) -> dict[str, object]:
        values: dict[str, object] = {}
        account_operations = frozenset(getattr(self._reader, "ACCOUNT_OPERATIONS", ()))
        for operation in ("markets", "portfolio", "fees", "hlp"):
            params = {"active_account": account} if operation in account_operations else {}
            result = self._reader(operation, params)
            if not isinstance(result, Mapping):
                raise RuntimeError("Optional module returned invalid public data")
            values[operation] = self._validated_result(
                self._module_id, self._reader_capability_id, operation, result,
            )
        history = self._action_adapter.history(account)
        if not isinstance(history, tuple):
            raise RuntimeError("Optional module returned invalid operation history")
        values["history"] = self._validated_result(
            self._module_id, self._reader_capability_id, "history",
            {"operations": list(history)},
        )["operations"]
        return values

    @Slot(result=bool)
    def refresh(self) -> bool:
        account = self._account()
        if self._busy or account is None:
            self._message = "Active Wallet account is unavailable"
            self.changed.emit()
            return False
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._message = "Refreshing public Hyperliquid data..."
        self.changed.emit()
        future = self._executor.submit(self._read_bundle, account)
        future.add_done_callback(
            lambda completed, current=generation: self._readReady.emit(current, completed),
        )
        return True

    @Slot(int, object)
    def _accept_read(self, generation: int, future: object) -> None:
        if generation != self._generation:
            return
        try:
            values = future.result()
            markets = values["markets"]
            portfolio = values["portfolio"]
            hlp = values["hlp"]
            fees = values["fees"]
            history = values["history"]
            self._markets = list(markets.get("markets", []))
            self._portfolio = dict(portfolio)
            self._hlp = dict(hlp)
            self._fees = dict(fees)
            self._history = list(history)
            unavailable = [
                name for name, value in (
                    ("markets", markets), ("portfolio", portfolio),
                    ("HLP", hlp), ("fees", fees),
                ) if value.get("status") != "READY"
            ]
            self._message = (
                "Public data ready" if not unavailable
                else "Unavailable: " + ", ".join(unavailable)
            )
        except Exception:
            self._message = "Public Hyperliquid data is unavailable"
        self._busy = False
        self.changed.emit()

    def _prepare(self, action_type: str, params: dict[str, object]) -> bool:
        if self._busy or self._prepared_internal is not None or self._account() is None:
            return False
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._message = "Building a fresh read-only preview..."
        self.changed.emit()
        future = self._executor.submit(
            self._action_client.module_action_preview,
            self._module_id, self._action_capability_id, action_type, params,
        )
        future.add_done_callback(
            lambda completed, current=generation: self._previewReady.emit(
                current, {"future": completed, "params": params},
            ),
        )
        return True

    @Slot(str, str, str, int, result=bool)
    def prepareOpenPosition(
        self, market: str, side: str, notional_usdc: str, leverage: int,
    ) -> bool:
        return self._prepare("OPEN_POSITION", {
            "market": market, "side": side,
            "notional_usdc": notional_usdc, "leverage": leverage,
        })

    @Slot(str, str, str, result=bool)
    def prepareClosePosition(
        self, market: str, amount_mode: str, percent: str,
    ) -> bool:
        return self._prepare("CLOSE_POSITION", {
            "market": market, "amount_mode": amount_mode,
            "percent": None if amount_mode == "FULL" else percent,
        })

    @Slot(str, result=bool)
    def prepareHlpDeposit(self, amount_usdc: str) -> bool:
        return self._prepare("HLP_DEPOSIT", {"amount_usdc": amount_usdc})

    @Slot(str, str, result=bool)
    def prepareHlpWithdraw(self, amount_mode: str, amount_usdc: str) -> bool:
        return self._prepare("HLP_WITHDRAW", {
            "amount_mode": amount_mode,
            "amount_usdc": None if amount_mode == "ALL" else amount_usdc,
        })

    @Slot(int, object)
    def _accept_preview(self, generation: int, context: object) -> None:
        if generation != self._generation or not isinstance(context, Mapping):
            return
        try:
            response = context["future"].result()
            payload = response.payload
            params = context["params"]
            if (
                response.kind is not MessageKind.MODULE_ACTION_PREVIEW
                or payload["status"] != "PREVIEW_READY"
                or payload["execution_available"] is not True
                or not isinstance(params, Mapping)
            ):
                raise RuntimeError
            internal = {
                "action_type": payload["action_type"], "params": dict(params),
                "preview_digest": payload["preview_digest"],
                "expires_at": payload["expires_at"],
            }
            self._prepared_internal = internal
            self._prepared = {
                **internal, "preview": dict(payload["preview"]),
                "checks": list(payload["checks"]),
                "caveats": list(payload["caveats"]),
                "message": payload["message"],
            }
            self._message = "Preview ready. Review it before opening Wallet confirmation."
        except Exception:
            self._prepared_internal = None
            self._prepared = {}
            self._message = "Protected action preview is unavailable or refused"
        self._busy = False
        self.changed.emit()

    @Slot()
    def cancelPrepared(self) -> None:
        if self._busy:
            return
        self._prepared_internal = None
        self._prepared = {}
        self._message = "Prepared preview cancelled"
        self.changed.emit()

    @Slot(result=bool)
    def executePrepared(self) -> bool:
        prepared = self._prepared_internal
        if self._busy or prepared is None:
            return False
        try:
            expires = datetime.fromisoformat(
                str(prepared["expires_at"]).removesuffix("Z") + "+00:00",
            )
        except ValueError:
            expires = datetime.min.replace(tzinfo=UTC)
        self._prepared_internal = None
        self._prepared = {}
        if expires <= datetime.now(UTC):
            self._message = "Preview expired. Build a new preview."
            self.changed.emit()
            return False
        if self._before_execute is not None:
            self._before_execute()
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._message = "Opening exact Wallet Review..."
        self.changed.emit()
        action_id = new_action_id()
        future = self._executor.submit(
            self._action_client.module_action_execute,
            self._module_id, self._action_capability_id,
            prepared["action_type"], prepared["params"],
            prepared["preview_digest"], action_id,
        )
        future.add_done_callback(
            lambda completed, current=generation: self._executeReady.emit(
                current, completed,
            ),
        )
        return True

    @Slot(int, object)
    def _accept_execute(self, generation: int, future: object) -> None:
        if generation != self._generation:
            return
        try:
            response = future.result()
            if response.kind is not MessageKind.PROTECTED_FLOW_STARTED:
                raise RuntimeError
            self._message = "Exact action opened in Wallet Review"
        except Exception:
            self._message = "Wallet Review could not be opened; nothing was submitted"
        self._busy = False
        self.changed.emit()


def _valid_text(model: Mapping[str, object], field: str, maximum: int) -> bool:
    value = model.get(field)
    return (
        isinstance(value, str) and bool(value) and len(value) <= maximum
        and not any(ord(character) < 32 and character not in "\n\t" for character in value)
    )


def module_page_to_map(
    registry: CapabilityRegistry, *, account_provider=None, action_client=None,
    executor: Executor | None = None, before_execute=None,
) -> dict[str, object]:
    capabilities = registry.capabilities("wallet_page")
    if len(capabilities) != 1:
        return {}
    capability = capabilities[0]
    if registry.module_status(capability.module_id).state is not ModuleLifecycleState.READY:
        return {}
    descriptor = capability.declaration.descriptor
    model = capability.contribution
    if not isinstance(model, Mapping) or model.get("moduleId") != capability.module_id:
        return {}
    basic_fields = {"body", "moduleId", "title"}
    interactive_fields = basic_fields | {"actionCapabilityId", "readCapabilityId"}
    model_fields = set(model)
    if model_fields != basic_fields and model_fields != interactive_fields:
        return {}
    if not _valid_text(model, "title", 80) or not _valid_text(model, "body", 512):
        return {}
    view_model: object = StaticModuleViewModel(
        capability.module_id, str(model["title"]), str(model["body"]),
    ).to_mapping()
    if model_fields == interactive_fields:
        if account_provider is None or action_client is None or executor is None:
            return {}
        read_id = model.get("readCapabilityId")
        action_id = model.get("actionCapabilityId")
        try:
            reader_capability = registry.resolve(str(read_id))
            action_adapter = registry.resolve(f"{capability.module_id}.action.wallet")
        except Exception:
            return {}
        if (
            read_id != f"{capability.module_id}.read.wallet"
            or action_id != f"{capability.module_id}.action.guard"
            or reader_capability.module_id != capability.module_id
            or reader_capability.declaration.kind != "public_reader"
            or set(reader_capability.declaration.descriptor.get("operations", ()))
            != {"fees", "hlp", "markets", "portfolio"}
            or not callable(reader_capability.contribution)
            or action_adapter.module_id != capability.module_id
            or action_adapter.declaration.kind != "protected_action_adapter"
            or not callable(getattr(action_adapter.contribution, "history", None))
        ):
            return {}
        view_model = ModuleViewModel(
            capability.module_id, str(model["title"]), str(model["body"]),
            reader=reader_capability.contribution,
            reader_capability_id=str(read_id), action_capability_id=str(action_id),
            action_adapter=action_adapter.contribution,
            account_provider=account_provider, action_client=action_client,
            executor=executor, before_execute=before_execute,
        )
    root = Path(capability.resource_root or "")
    qml_path = root.joinpath(*str(descriptor["qml_path"]).split("/"))
    if not qml_path.is_file():
        return {}
    icon_source = str(descriptor["icon_source"])
    icon_url = ""
    if icon_source:
        icon_path = root.joinpath(*icon_source.split("/"))
        if not icon_path.is_file():
            return {}
        icon_url = icon_path.resolve().as_uri()
    return {
        "available": True, "iconSource": icon_url,
        "label": str(descriptor["label"]), "model": view_model,
        "moduleId": capability.module_id, "qmlUrl": qml_path.resolve().as_uri(),
        "route": str(descriptor["route"]),
    }
