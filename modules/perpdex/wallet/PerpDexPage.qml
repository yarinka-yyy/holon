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
    property var liveMarkets: []

    function choose(current, value) { return current === value }
    function money(value) {
        return value === undefined || value === null || value === "" ? "—" : value + " USDC"
    }
    function positionNotional(position) {
        const value = Number(position.size_asset) * Number(position.entry_price)
        return Number.isFinite(value) ? "≈ " + value.toFixed(2) + " USDC" : "—"
    }
    function positionPrice(value) {
        return value === undefined || value === null || value === "" ? "—" : "$" + value
    }
    function notionalFromMargin(value) {
        const number = Number(value) * root.leverage
        return Number.isFinite(number) && number > 0 ? number.toFixed(6).replace(/0+$/, "").replace(/\.$/, "") : ""
    }
    function refresh() {
        if (root.moduleViewModel && !root.moduleViewModel.busy)
            root.moduleViewModel.refresh()
    }
    function selectedMarketData() {
        for (let index = 0; index < root.liveMarkets.length; ++index)
            if (root.liveMarkets[index].market === root.market)
                return root.liveMarkets[index]
        return null
    }
    function marketMaxLeverage() {
        const current = root.selectedMarketData()
        if (current)
            return Math.max(1, Number(current.max_exchange_leverage || 1))
        // Keep the intended 2x default while market metadata is still loading.
        // Once it arrives, onCurrentMaxLeverageChanged clamps it to the live limit.
        return 2
    }
    property int currentMaxLeverage: marketMaxLeverage()
    function clampLeverage(value) {
        return Math.max(1, Math.min(root.currentMaxLeverage, Math.round(value)))
    }
    function positiveDecimal(value) {
        return /^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$/.test(value)
            && !/^0(?:\.0+)?$/.test(value)
    }
    function validClosePercent(value) {
        return root.positiveDecimal(value) && value.split(".")[0].length < 3
    }
    function selectedMarketHasPosition() {
        const positions = root.moduleViewModel ? (root.moduleViewModel.portfolio.positions || []) : []
        return positions.some(item => item.market === root.market && item.supported)
    }
    function selectedMarketHasOrders() {
        const orders = root.moduleViewModel ? (root.moduleViewModel.portfolio.orders || []) : []
        return orders.some(item => item.market === root.market)
    }
    function canPrepareTrade() {
        if (!root.moduleViewModel || root.moduleViewModel.busy || !root.selectedMarketData())
            return false
        if (root.tradeMode === "OPEN")
            return root.positiveDecimal(openNotional.text)
                && !root.selectedMarketHasPosition() && !root.selectedMarketHasOrders()
        return root.selectedMarketHasPosition()
            && (root.closeMode === "FULL" || root.validClosePercent(closePercent.text))
    }
    onMarketChanged: root.leverage = root.clampLeverage(root.leverage)
    onCurrentMaxLeverageChanged: root.leverage = root.clampLeverage(root.leverage)
    onLiveMarketsChanged: root.leverage = root.clampLeverage(root.leverage)
    onModuleViewModelChanged: root.liveMarkets = root.moduleViewModel
        ? root.moduleViewModel.markets : []
    Connections {
        target: root.moduleViewModel
        function onChanged() {
            root.liveMarkets = root.moduleViewModel ? root.moduleViewModel.markets : []
        }
    }
    Component.onCompleted: {
        root.liveMarkets = root.moduleViewModel ? root.moduleViewModel.markets : []
        root.refresh()
    }

    Flickable {
        id: scroll
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: content.height + 46
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOff }

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
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
                }
                Text {
                    x: 16; y: 68
                    text: root.money(root.moduleViewModel ? root.moduleViewModel.portfolio.account_equity_usdc : null)
                    color: Design.text; font.family: Design.fontFamily; font.pixelSize: 18
                }
                Text {
                    x: 242; y: 47; text: "Available collateral"
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
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
                            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
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
                    width: content.width; height: 124
                    Text {
                        x: 16; y: 14
                        text: modelData.market + " · " + modelData.side
                            + (modelData.supported ? "" : " · READ ONLY")
                        color: modelData.supported ? Design.text : Design.warning
                        font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
                    }
                    Text {
                        x: 16; y: 45
                        text: modelData.size_asset + " " + modelData.market + " · " + root.positionNotional(modelData)
                        color: Design.text; font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
                    }
                    Text {
                        x: 16; y: 74
                        text: modelData.leverage_type + " · " + modelData.leverage_value + "x · Margin " + root.money(modelData.margin_used_usdc)
                        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
                    }
                    Text {
                        x: 16; y: 96; width: 270
                        text: "Entry " + root.positionPrice(modelData.entry_price) + " · Liquidation " + root.positionPrice(modelData.liquidation_price)
                        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10; elide: Text.ElideRight
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 16; y: 20
                        text: "PnL\n" + modelData.unrealized_pnl_usdc + " USDC"
                        horizontalAlignment: Text.AlignRight; color: Number(modelData.unrealized_pnl_usdc) >= 0 ? Design.accent : Design.danger
                        font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.DemiBold
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 16; y: 74
                        text: "Live position"; color: Design.textFaint
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
                height: tradeForm.height + 32
                Column {
                    id: tradeForm
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
                    Text {
                        text: "Market"; color: Design.textMuted
                        font.family: Design.fontFamily; font.pixelSize: 12; font.weight: Font.Medium
                    }
                    Row {
                        spacing: 8
                        Repeater {
                            model: ["BTC", "ETH", "SOL"]
                            delegate: FormButton {
                                required property string modelData
                                objectName: "perpDexMarket-" + modelData
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
                            width: parent.width; height: 70; label: "Margin in USDC"
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
                                font.family: Design.fontFamily; font.pixelSize: 12; font.weight: Font.Medium
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
                        width: parent.width
                        height: root.tradeMode === "OPEN"
                            ? (root.marginMode === "CROSS" ? 112 : 70) : 0
                        visible: root.tradeMode === "OPEN"
                        Text {
                            text: "Leverage · 1x–" + root.currentMaxLeverage + "x"
                            color: Design.textMuted; font.family: Design.fontFamily
                            font.pixelSize: 12; font.weight: Font.Medium
                        }
                        Item {
                            id: leverageSlider; objectName: "perpDexLeverageSlider"
                            x: 0; y: 20; width: parent.width - 98; height: 38
                            property int from: 1
                            property int to: root.currentMaxLeverage
                            property int value: root.leverage
                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width; height: 4; radius: 2
                                color: Design.border
                                Rectangle {
                                    width: (root.leverage - leverageSlider.from)
                                        / Math.max(1, leverageSlider.to - leverageSlider.from) * parent.width
                                    height: parent.height; radius: parent.radius; color: Design.accent
                                }
                            }
                            Rectangle {
                                x: (root.leverage - leverageSlider.from)
                                    / Math.max(1, leverageSlider.to - leverageSlider.from) * (parent.width - width)
                                anchors.verticalCenter: parent.verticalCenter
                                width: 20; height: 20; radius: 10
                                color: Design.accent; border.width: 2; border.color: Design.surface
                                Rectangle {
                                    anchors.centerIn: parent; width: 5; height: 5; radius: 3
                                    color: Design.textOnAccent
                                }
                            }
                            MouseArea {
                                anchors.fill: parent; hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                function updateValue(position) {
                                    const fraction = Math.max(0, Math.min(1, position / width))
                                    root.leverage = root.clampLeverage(
                                        leverageSlider.from + fraction
                                        * (leverageSlider.to - leverageSlider.from),
                                    )
                                }
                                onPressed: event => updateValue(event.x)
                                onPositionChanged: event => {
                                    if (pressed)
                                        updateValue(event.x)
                                }
                            }
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
                            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 11
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
                        color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 11
                    }
                    FormButton {
                        objectName: "perpDexTradePrepareButton"; width: parent.width; height: 46
                        label: "Build fresh preview"
                        controlEnabled: root.canPrepareTrade()
                        onTriggered: {
                            if (root.tradeMode === "OPEN")
                                root.moduleViewModel.prepareOpenPosition(
                                    root.market, root.side, root.notionalFromMargin(openNotional.text),
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
                width: parent.width; height: visible ? 176 : 0
                Text {
                    x: 16; y: 14; text: "Fresh review is ready"
                    color: Design.text; font.family: Design.fontFamily
                    font.pixelSize: 15; font.weight: Font.DemiBold
                }
                Text {
                    x: 16; y: 48; width: parent.width - 32; wrapMode: Text.Wrap
                    text: "The Wallet will show the exact position and live protections before it asks for your local password."
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10
                    maximumLineCount: 4; elide: Text.ElideRight
                }
                Text {
                    x: 16; y: 98; width: parent.width - 32
                    text: "Nothing has been signed or submitted."
                    wrapMode: Text.Wrap; color: Design.warning
                    font.family: Design.fontFamily; font.pixelSize: 10
                }
                FormButton {
                    objectName: "perpDexExecutePreparedButton"
                    x: 16; y: 126; width: 276; height: 36; label: "Open Wallet Review"
                    onTriggered: root.moduleViewModel.executePrepared()
                }
                FormButton {
                    x: 300; y: 126; width: 142; height: 36; label: "Cancel"; primary: false
                    onTriggered: root.moduleViewModel.cancelPrepared()
                }
            }

            Text {
                text: "Active orders"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
            }
            Text {
                visible: !root.moduleViewModel || (root.moduleViewModel.portfolio.orders || []).length === 0
                text: "Unfinished orders only. Your IOC orders normally do not stay active."; color: Design.textMuted
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

        }
    }

    ScrollCue {
        objectName: "perpDexScrollCue"
        anchors.right: parent.right; anchors.rightMargin: 12
        anchors.bottom: parent.bottom; anchors.bottomMargin: 12
        suggested: scroll.contentHeight > scroll.height
            && scroll.contentY < scroll.contentHeight - scroll.height - 2
    }
}
