import QtQuick
import QtQuick.Controls
import "."

Item {
    id: root
    objectName: "perpDexModulePage"
    property var moduleViewModel: null
    property string market: "BTC"
    property string side: "LONG"
    property int leverage: 2
    property string marginMode: "ISOLATED"
    property string tradeMode: "OPEN"
    property string closeMode: "FULL"
    property string withdrawMode: "ALL"

    function choose(current, value) { return current === value }
    function money(value) {
        return value === undefined || value === null || value === "" ? "—" : value + " USDC"
    }
    function refresh() {
        if (root.moduleViewModel && !root.moduleViewModel.busy)
            root.moduleViewModel.refresh()
    }
    function marketMaxLeverage() {
        const markets = root.moduleViewModel ? root.moduleViewModel.markets : []
        for (let index = 0; index < markets.length; ++index)
            if (markets[index].market === root.market)
                return Math.max(1, Number(markets[index].max_exchange_leverage || 1))
        // Keep the intended 2x default while market metadata is still loading.
        // Once it arrives, onCurrentMaxLeverageChanged clamps it to the live limit.
        return 2
    }
    property int currentMaxLeverage: marketMaxLeverage()
    function clampLeverage(value) {
        return Math.max(1, Math.min(root.currentMaxLeverage, Math.round(value)))
    }
    onMarketChanged: root.leverage = root.clampLeverage(root.leverage)
    onCurrentMaxLeverageChanged: root.leverage = root.clampLeverage(root.leverage)
    Component.onCompleted: refresh()

    Flickable {
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: content.height + 46
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
            id: content
            x: 28; y: 10; width: parent.width - 56; spacing: 14

            Row {
                width: parent.width; height: 38; spacing: 12
                Text {
                    width: parent.width - 116; anchors.verticalCenter: parent.verticalCenter
                    text: root.moduleViewModel ? root.moduleViewModel.statusMessage : "Module unavailable"
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
                    elide: Text.ElideRight
                }
                FormButton {
                    objectName: "perpDexRefreshButton"; width: 104; height: 38
                    label: root.moduleViewModel && root.moduleViewModel.busy ? "Working…" : "Refresh"
                    primary: false
                    controlEnabled: root.moduleViewModel && !root.moduleViewModel.busy
                    onTriggered: root.refresh()
                }
            }

            SurfaceCard {
                objectName: "perpDexAccountCard"; width: parent.width; height: 118
                Text {
                    x: 16; y: 14; text: "Hyperliquid trading account"
                    color: Design.text; font.family: Design.fontFamily
                    font.pixelSize: 15; font.weight: Font.DemiBold
                }
                Text {
                    x: 16; y: 47; text: "Account equity"
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
                }
                Text {
                    x: 16; y: 68
                    text: root.money(root.moduleViewModel ? root.moduleViewModel.portfolio.account_equity_usdc : null)
                    color: Design.text; font.family: Design.fontFamily; font.pixelSize: 18
                }
                Text {
                    x: 242; y: 47; text: "Available collateral"
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
                }
                Text {
                    x: 242; y: 68
                    text: root.money(root.moduleViewModel ? root.moduleViewModel.portfolio.withdrawable_usdc : null)
                    color: Design.accent; font.family: Design.fontFamily; font.pixelSize: 18
                }
            }

            Text {
                text: "Markets"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
            }
            Row {
                width: parent.width; spacing: 9
                Repeater {
                    model: root.moduleViewModel ? root.moduleViewModel.markets : []
                    delegate: SurfaceCard {
                        required property var modelData
                        width: (content.width - 18) / 3; height: 96
                        Text {
                            x: 12; y: 11; text: modelData.market
                            color: Design.text; font.family: Design.fontFamily
                            font.pixelSize: 16; font.weight: Font.DemiBold
                        }
                        Text {
                            x: 12; y: 40; text: "$" + modelData.mark_price
                            color: Design.accent; font.family: Design.fontFamily; font.pixelSize: 14
                        }
                        Text {
                            x: 12; y: 67
                            text: "Spread " + (modelData.spread_percent || "—") + "%"
                            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10
                        }
                    }
                }
            }

            Text {
                text: "Positions"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
            }
            Text {
                visible: !root.moduleViewModel || (root.moduleViewModel.portfolio.positions || []).length === 0
                text: "No open positions"; color: Design.textMuted
                font.family: Design.fontFamily; font.pixelSize: 12
            }
            Repeater {
                model: root.moduleViewModel ? (root.moduleViewModel.portfolio.positions || []) : []
                delegate: SurfaceCard {
                    required property var modelData
                    width: content.width; height: 88
                    Text {
                        x: 14; y: 12
                        text: modelData.market + " · " + modelData.side
                            + (modelData.supported ? "" : " · READ ONLY")
                        color: modelData.supported ? Design.text : Design.warning
                        font.family: Design.fontFamily; font.pixelSize: 14; font.weight: Font.DemiBold
                    }
                    Text {
                        x: 14; y: 42
                        text: "Size " + modelData.size_asset + " · Entry " + (modelData.entry_price || "—")
                        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
                    }
                    Text {
                        x: 14; y: 64
                        text: "PnL " + modelData.unrealized_pnl_usdc + " USDC"
                        color: Number(modelData.unrealized_pnl_usdc) >= 0 ? Design.accent : Design.danger
                        font.family: Design.fontFamily; font.pixelSize: 11
                    }
                }
            }

            Text {
                text: "Trade BTC / ETH / SOL"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
            }
            SurfaceCard {
                objectName: "perpDexTradeCard"; width: parent.width
                height: root.tradeMode === "OPEN" ? 616 : 360
                Column {
                    x: 16; y: 16; width: parent.width - 32; spacing: 12
                    Row {
                        spacing: 8
                        Repeater {
                            model: ["OPEN", "CLOSE"]
                            delegate: FormButton {
                                required property string modelData
                                width: 202; height: 40; label: modelData === "OPEN" ? "Open position" : "Close position"
                                primary: root.tradeMode === modelData
                                onTriggered: root.tradeMode = modelData
                            }
                        }
                    }
                    Text { text: "Market"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
                    Row {
                        spacing: 8
                        Repeater {
                            model: ["BTC", "ETH", "SOL"]
                            delegate: FormButton {
                                required property string modelData
                                width: 132; height: 38; label: modelData
                                primary: root.market === modelData
                                onTriggered: root.market = modelData
                            }
                        }
                    }
                    Item {
                        width: parent.width; height: root.tradeMode === "OPEN" ? 48 : 0
                        visible: root.tradeMode === "OPEN"
                        Row {
                            spacing: 8
                            FormButton {
                                width: 202; height: 40; label: "LONG"; primary: root.side === "LONG"
                                onTriggered: root.side = "LONG"
                            }
                            FormButton {
                                width: 202; height: 40; label: "SHORT"; primary: root.side === "SHORT"
                                onTriggered: root.side = "SHORT"
                            }
                        }
                    }
                    Item {
                        width: parent.width; height: root.tradeMode === "OPEN" ? 76 : 0
                        visible: root.tradeMode === "OPEN"
                        DraftField {
                            id: openNotional; objectName: "perpDexOpenNotional"
                            width: parent.width; height: 70; label: "Notional in USDC"
                            placeholderText: "25"
                        }
                    }
                    Item {
                        width: parent.width; height: root.tradeMode === "OPEN" ? 62 : 0
                        visible: root.tradeMode === "OPEN"
                        Column {
                            spacing: 7
                            Text {
                                text: "Margin mode"; color: Design.textMuted
                                font.family: Design.fontFamily; font.pixelSize: 11
                            }
                            Row {
                                spacing: 8
                                FormButton {
                                    width: 202; height: 38; label: "Isolated"
                                    primary: root.marginMode === "ISOLATED"
                                    onTriggered: root.marginMode = "ISOLATED"
                                }
                                FormButton {
                                    width: 202; height: 38; label: "Cross"
                                    primary: root.marginMode === "CROSS"
                                    onTriggered: root.marginMode = "CROSS"
                                }
                            }
                        }
                    }
                    Item {
                        width: parent.width; height: root.tradeMode === "OPEN" ? 112 : 0
                        visible: root.tradeMode === "OPEN"
                        Text {
                            text: "Leverage · 1x–" + root.currentMaxLeverage + "x"
                            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
                        }
                        Slider {
                            id: leverageSlider
                            x: 0; y: 22; width: parent.width - 98; height: 32
                            from: 1; to: root.currentMaxLeverage; stepSize: 1
                            value: root.leverage
                            onMoved: root.leverage = root.clampLeverage(value)
                        }
                        Rectangle {
                            x: parent.width - 86; y: 18; width: 86; height: 40; radius: Design.controlRadius
                            color: Design.surface; border.width: leverageInput.activeFocus ? 2 : 1
                            border.color: leverageInput.activeFocus ? Design.accent : Design.border
                            TextInput {
                                id: leverageInput; x: 12; width: parent.width - 24
                                anchors.verticalCenter: parent.verticalCenter
                                text: root.leverage + "x"; color: Design.text
                                font.family: Design.fontFamily; font.pixelSize: 13
                                validator: RegularExpressionValidator { regularExpression: /[1-9][0-9]*x?/ }
                                onEditingFinished: {
                                    root.leverage = root.clampLeverage(Number(text.replace("x", "")))
                                    text = root.leverage + "x"
                                }
                            }
                            Connections {
                                target: root
                                function onLeverageChanged() {
                                    if (!leverageInput.activeFocus)
                                        leverageInput.text = root.leverage + "x"
                                }
                            }
                        }
                        Text {
                            y: 70; width: parent.width; wrapMode: Text.Wrap
                            visible: root.marginMode === "CROSS"
                            text: "Cross shares your PerpDEX collateral with cross positions. Review the account-wide risk before continuing."
                            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 10
                        }
                    }
                    Item {
                        width: parent.width; height: root.tradeMode === "CLOSE" ? 48 : 0
                        visible: root.tradeMode === "CLOSE"
                        Row {
                            spacing: 8
                            FormButton {
                                width: 202; height: 40; label: "Close full"
                                primary: root.closeMode === "FULL"
                                onTriggered: root.closeMode = "FULL"
                            }
                            FormButton {
                                width: 202; height: 40; label: "Close percent"
                                primary: root.closeMode === "PERCENT"
                                onTriggered: root.closeMode = "PERCENT"
                            }
                        }
                    }
                    Item {
                        width: parent.width; height: root.tradeMode === "CLOSE" && root.closeMode === "PERCENT" ? 76 : 0
                        visible: height > 0
                        DraftField {
                            id: closePercent; objectName: "perpDexClosePercent"
                            width: parent.width; height: 70; label: "Percent (greater than 0, less than 100)"
                            placeholderText: "50"
                        }
                    }
                    Text {
                        width: parent.width; wrapMode: Text.Wrap
                        text: root.tradeMode === "OPEN"
                            ? "Market IOC · maximum slippage 1% · flat position only · no open orders"
                            : "Reduce-only · current market orders are shown and cancelled in Review"
                        color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 10
                    }
                    FormButton {
                        objectName: "perpDexTradePrepareButton"; width: parent.width; height: 46
                        label: "Build fresh preview"
                        controlEnabled: root.moduleViewModel && !root.moduleViewModel.busy
                        onTriggered: {
                            if (root.tradeMode === "OPEN")
                                root.moduleViewModel.prepareOpenPosition(
                                    root.market, root.side, openNotional.text,
                                    root.leverage, root.marginMode,
                                )
                            else
                                root.moduleViewModel.prepareClosePosition(root.market, root.closeMode, closePercent.text)
                        }
                    }
                }
            }

            SurfaceCard {
                objectName: "perpDexPreparedCard"
                visible: root.moduleViewModel && Object.keys(root.moduleViewModel.prepared || {}).length > 0
                width: parent.width; height: visible ? 214 : 0
                Text {
                    x: 16; y: 14; text: "Prepared · " + (root.moduleViewModel ? root.moduleViewModel.prepared.action_type : "")
                    color: Design.text; font.family: Design.fontFamily
                    font.pixelSize: 15; font.weight: Font.DemiBold
                }
                Text {
                    x: 16; y: 48; width: parent.width - 32; wrapMode: Text.Wrap
                    text: root.moduleViewModel
                        ? JSON.stringify(root.moduleViewModel.prepared.preview || {}) : ""
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10
                    maximumLineCount: 4; elide: Text.ElideRight
                }
                Text {
                    x: 16; y: 112; width: parent.width - 32
                    text: "This is read-only. The exact bundle still requires trusted Wallet Review and a fresh password."
                    wrapMode: Text.Wrap; color: Design.warning
                    font.family: Design.fontFamily; font.pixelSize: 10
                }
                FormButton {
                    objectName: "perpDexExecutePreparedButton"
                    x: 16; y: 158; width: 276; height: 42; label: "Open exact Wallet Review"
                    onTriggered: root.moduleViewModel.executePrepared()
                }
                FormButton {
                    x: 300; y: 158; width: 142; height: 42; label: "Cancel"; primary: false
                    onTriggered: root.moduleViewModel.cancelPrepared()
                }
            }

            Text {
                text: "Official HLP"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
            }
            SurfaceCard {
                objectName: "perpDexHlpCard"; width: parent.width; height: 398
                Text {
                    x: 16; y: 14; text: "Hyperliquidity Provider (parent vault)"
                    color: Design.text; font.family: Design.fontFamily
                    font.pixelSize: 15; font.weight: Font.DemiBold
                }
                Text {
                    x: 16; y: 48
                    text: "Equity " + root.money(root.moduleViewModel ? root.moduleViewModel.hlp.equity_usdc : null)
                    color: Design.accent; font.family: Design.fontFamily; font.pixelSize: 14
                }
                Text {
                    x: 16; y: 74
                    text: "PnL " + root.money(root.moduleViewModel ? root.moduleViewModel.hlp.pnl_usdc : null)
                        + " · Protocol APR " + (root.moduleViewModel ? (root.moduleViewModel.hlp.protocol_apr_percent || "—") : "—") + "%"
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
                }
                Text {
                    x: 16; y: 101; width: parent.width - 32; wrapMode: Text.Wrap
                    text: "Risk: NOT ASSESSED · Four-day lock-up; every new deposit restarts it."
                    color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 10
                }
                DraftField {
                    id: hlpDeposit; objectName: "perpDexHlpDepositAmount"
                    x: 16; y: 138; width: parent.width - 32; height: 70
                    label: "Deposit USDC"; placeholderText: "25"
                }
                FormButton {
                    objectName: "perpDexHlpDepositPrepareButton"
                    x: 16; y: 216; width: parent.width - 32; height: 42
                    label: "Build deposit preview"
                    controlEnabled: root.moduleViewModel && !root.moduleViewModel.busy
                    onTriggered: root.moduleViewModel.prepareHlpDeposit(hlpDeposit.text)
                }
                Row {
                    x: 16; y: 274; spacing: 8
                    FormButton {
                        width: 202; height: 38; label: "Withdraw all"
                        primary: root.withdrawMode === "ALL"
                        onTriggered: root.withdrawMode = "ALL"
                    }
                    FormButton {
                        width: 202; height: 38; label: "Withdraw exact"
                        primary: root.withdrawMode === "EXACT"
                        onTriggered: root.withdrawMode = "EXACT"
                    }
                }
                DraftField {
                    id: hlpWithdraw; objectName: "perpDexHlpWithdrawAmount"
                    x: 16; y: 320; width: 240; height: 70
                    visible: root.withdrawMode === "EXACT"
                    label: "Exact unlocked USDC"; placeholderText: "10"
                }
                FormButton {
                    objectName: "perpDexHlpWithdrawPrepareButton"
                    x: root.withdrawMode === "EXACT" ? 266 : 16; y: 334
                    width: root.withdrawMode === "EXACT" ? 176 : parent.width - 32
                    height: 46; label: "Build withdraw preview"
                    controlEnabled: root.moduleViewModel && !root.moduleViewModel.busy
                    onTriggered: root.moduleViewModel.prepareHlpWithdraw(root.withdrawMode, hlpWithdraw.text)
                }
            }

            Text {
                text: "Open orders"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
            }
            Text {
                visible: !root.moduleViewModel || (root.moduleViewModel.portfolio.orders || []).length === 0
                text: "No open orders"; color: Design.textMuted
                font.family: Design.fontFamily; font.pixelSize: 12
            }
            Repeater {
                model: root.moduleViewModel ? (root.moduleViewModel.portfolio.orders || []) : []
                delegate: SurfaceCard {
                    required property var modelData
                    width: content.width; height: 66
                    Text {
                        x: 14; y: 11
                        text: modelData.market + " · " + modelData.side + " · " + modelData.size_asset
                        color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
                    }
                    Text {
                        x: 14; y: 37
                        text: "Limit " + modelData.limit_price + (modelData.reduce_only ? " · reduce-only" : "")
                        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10
                    }
                }
            }

            Text {
                text: "Operation history"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
            }
            Text {
                visible: !root.moduleViewModel || root.moduleViewModel.operationHistory.length === 0
                text: "No Holon PerpDEX operations yet"; color: Design.textMuted
                font.family: Design.fontFamily; font.pixelSize: 12
            }
            Repeater {
                model: root.moduleViewModel ? root.moduleViewModel.operationHistory : []
                delegate: SurfaceCard {
                    required property var modelData
                    width: content.width; height: 72
                    Text {
                        x: 14; y: 11; text: modelData.action_type
                        color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 14; y: 11
                        text: modelData.state
                        color: modelData.state === "COMPLETED" ? Design.accent : Design.warning
                        font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold
                    }
                    Text {
                        x: 14; y: 41; width: parent.width - 28; elide: Text.ElideMiddle
                        text: modelData.updated_at + " · " + modelData.operation_id
                        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 9
                    }
                }
            }
        }
    }
}
