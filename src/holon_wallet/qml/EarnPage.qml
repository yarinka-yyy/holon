import QtQuick
import "."

PageState {
    id: root
    property string chartMode: "position"
    property bool showAllProtocols: false
    property var primaryProtocols: walletController.lendingData.visibleProtocols || []
    property var emptyProtocols: walletController.lendingData.emptyProtocols || []
    property int hiddenProtocolCount: walletController.lendingData.hiddenProtocolCount || 0
    property bool showLending: walletController.earnData.showLending
    property bool showVaults: walletController.earnData.showVaults
    property var vaultProducts: walletController.earnData.vaultProducts || []
    function protocolModel() {
        return showAllProtocols ? primaryProtocols.concat(emptyProtocols) : primaryProtocols
    }
    onActiveChanged: if (active) showAllProtocols = false

    ScreenHeader {
        objectName: "earnHeader"; x: 28; y: 42; width: parent.width - 56
        title: "Earn"; subtitle: "Compare Lending and available Vaults"
        onBackRequested: walletController.showMain()
    }

    SurfaceCard {
        objectName: "lendingPreflightNotice"
        visible: walletController.lendingNotice.length > 0
        x: 28; y: 112; width: 458; height: 56
        color: "#251B12"; border.width: 1; border.color: Design.warning
        Text {
            x: 14; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 28
            text: walletController.lendingNotice
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
            wrapMode: Text.WordWrap; maximumLineCount: 2
        }
    }

    Row {
        id: earnFilters; objectName: "earnFilters"
        x: 28; y: walletController.lendingNotice.length > 0 ? 182 : 112
        spacing: 8
        Repeater {
            model: walletController.earnData.availableFilters || []
            delegate: Rectangle {
                required property var modelData
                objectName: "earnFilter-" + modelData.id
                width: 88; height: 34; radius: 11
                property bool selected: walletController.earnFilter === modelData.id
                color: selected ? Design.accentSoft : Design.surfaceSecondary
                border.width: 1; border.color: selected ? Design.accent : Design.border
                Text {
                    anchors.centerIn: parent; text: modelData.label
                    color: parent.selected ? Design.accent : Design.textMuted
                    font.family: Design.fontFamily; font.pixelSize: 12
                }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: walletController.selectEarnFilter(modelData.id)
                }
            }
        }
    }

    Flickable {
        id: scroll
        x: 0; y: walletController.lendingNotice.length > 0 ? 230 : 160
        width: parent.width; height: parent.height - y - 6
        contentWidth: width; contentHeight: content.height + 28
        clip: true; boundsBehavior: Flickable.StopAtBounds

        Item {
            id: content; width: scroll.width
            height: Math.max(
                root.showLending ? protocolColumn.y + protocolColumn.height : 0,
                root.showVaults ? vaultColumn.y + vaultColumn.height : 0
            ) + 86

            Item {
                objectName: "lendingRefreshButton"
                anchors.right: parent.right; anchors.rightMargin: 28
                y: 0; width: 126; height: 34
                function trigger() { walletController.refreshEarnData(true) }
                Text {
                    anchors.right: refreshIcon.left; anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter; text: "Refresh now"
                    color: refreshMouse.containsMouse ? Design.accent : Design.textMuted
                    font.family: Design.fontFamily; font.pixelSize: 13
                }
                Image {
                    id: refreshIcon; anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 19; height: 19; source: "assets/refresh.svg"
                    RotationAnimation on rotation {
                        running: walletController.earnDataRefreshing
                        from: 0; to: 360; duration: 800; loops: Animation.Infinite
                    }
                }
                MouseArea {
                    id: refreshMouse; anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor; onClicked: parent.trigger()
                }
            }

            Text {
                x: 28; y: 9; text: walletController.earnData.updatedText
                color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 12
            }

            SurfaceCard {
                objectName: "lendingSummaryCard"
                visible: root.showLending
                x: 28; y: 44; width: 458; height: 132
                Row {
                    x: 18; y: 20; spacing: 10
                    Repeater {
                        model: [
                            {title: "Total position", value: walletController.lendingData.totalPosition,
                             detail: walletController.lendingData.totalUsd},
                            {title: "Tracked earnings", value: walletController.lendingData.trackedEarnings,
                             detail: walletController.lendingData.earningsAvailable ? "Since tracking began" : "Starts from first reliable read"},
                            {title: "Weighted yield", value: walletController.lendingData.weightedYield,
                             detail: walletController.lendingData.yieldCompleteness === "COMPLETE" ? "Confirmed total" : "Incomplete data"}
                        ]
                        delegate: Item {
                            required property var modelData
                            width: 133; height: 94
                            Text {
                                width: parent.width; text: modelData.title; color: Design.textFaint
                                font.family: Design.fontFamily; font.pixelSize: 11
                            }
                            Text {
                                y: 25; width: parent.width; text: walletController.balancesVisible ? modelData.value : "••••"
                                color: Design.text; font.family: Design.fontFamily; font.pixelSize: 18
                                font.weight: Font.DemiBold; elide: Text.ElideRight
                            }
                            Text {
                                y: 57; width: parent.width; text: modelData.detail
                                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10
                                wrapMode: Text.WordWrap; maximumLineCount: 2
                            }
                        }
                    }
                }
            }

            Row {
                id: chartModes; visible: root.showLending; x: 28; y: 194; spacing: 8
                Repeater {
                    model: [{id: "position", label: "Position"}, {id: "yield", label: "Yield"}]
                    delegate: Rectangle {
                        required property var modelData
                        width: 92; height: 34; radius: 11
                        color: root.chartMode === modelData.id ? Design.accentSoft : Design.surfaceSecondary
                        border.width: 1; border.color: root.chartMode === modelData.id ? Design.accent : Design.border
                        Text {
                            anchors.centerIn: parent; text: modelData.label
                            color: root.chartMode === modelData.id ? Design.accent : Design.textMuted
                            font.family: Design.fontFamily; font.pixelSize: 12; font.weight: Font.Medium
                        }
                        MouseArea {
                            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                            onClicked: { root.chartMode = modelData.id; chart.requestPaint() }
                        }
                    }
                }
            }

            Row {
                id: historyPeriods; visible: root.showLending
                x: 304; y: 194; spacing: 6
                Repeater {
                    model: [{id: "7d", label: "7D"}, {id: "30d", label: "30D"}, {id: "all", label: "All"}]
                    delegate: Rectangle {
                        required property var modelData
                        width: 56; height: 34; radius: 11
                        property bool selected: walletController.lendingHistoryPeriod === modelData.id
                        color: selected ? Design.accentSoft : Design.surfaceSecondary
                        border.width: 1; border.color: selected ? Design.accent : Design.border
                        Text {
                            anchors.centerIn: parent; text: modelData.label
                            color: parent.selected ? Design.accent : Design.textMuted
                            font.family: Design.fontFamily; font.pixelSize: 12
                        }
                        MouseArea {
                            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                            onClicked: walletController.setLendingHistoryPeriod(modelData.id)
                        }
                    }
                }
            }

            SurfaceCard {
                objectName: "lendingChartCard"
                visible: root.showLending
                x: 28; y: 242; width: 458; height: 258
                Text {
                    x: 16; y: 13
                    text: root.chartMode === "position" ? "Position and earnings · USDC" : "Confirmed annual yield · %"
                    color: Design.text; font.family: Design.fontFamily; font.pixelSize: 13
                    font.weight: Font.Medium
                }
                Canvas {
                    id: chart; objectName: "lendingHistoryChart"
                    x: 16; y: 45; width: parent.width - 32; height: 140
                    property var points: walletController.lendingData.history.points || []
                    property string periodStart: walletController.lendingData.history.periodStart || ""
                    property string periodEnd: walletController.lendingData.history.periodEnd || ""
                    onPointsChanged: requestPaint()
                    onPeriodStartChanged: requestPaint()
                    onPeriodEndChanged: requestPaint()
                    onWidthChanged: requestPaint()
                    onHeightChanged: requestPaint()
                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.clearRect(0, 0, width, height)
                        ctx.strokeStyle = "#263139"
                        ctx.lineWidth = 1
                        for (var grid = 0; grid < 4; ++grid) {
                            var gy = 8 + grid * (height - 16) / 3
                            ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(width, gy); ctx.stroke()
                        }
                        if (points.length < 2)
                            return
                        var keys = root.chartMode === "position"
                            ? ["position", "earnings"] : ["aave", "compound", "morpho"]
                        var colors = root.chartMode === "position"
                            ? ["#84C7BA", "#D5AA64"] : ["#B4A1FF", "#00D395", "#6C8CFF"]
                        var values = []
                        for (var p = 0; p < points.length; ++p)
                            for (var k = 0; k < keys.length; ++k)
                                if (points[p][keys[k]] !== null && points[p][keys[k]] !== undefined)
                                    values.push(Number(points[p][keys[k]]))
                        if (!values.length)
                            return
                        var minimum = Math.min.apply(Math, values)
                        var maximum = Math.max.apply(Math, values)
                        if (maximum === minimum) { maximum += 0.5; minimum -= 0.5 }
                        for (var series = 0; series < keys.length; ++series) {
                            ctx.strokeStyle = colors[series]; ctx.lineWidth = 2
                            ctx.beginPath(); var started = false
                            for (var i = 0; i < points.length; ++i) {
                                var value = points[i][keys[series]]
                                if (value === null || value === undefined) { started = false; continue }
                                var firstTime = Date.parse(periodStart)
                                var lastTime = Date.parse(periodEnd)
                                var pointTime = Date.parse(points[i].observedAt)
                                var x = isNaN(firstTime) || isNaN(lastTime) || isNaN(pointTime)
                                    || lastTime <= firstTime
                                    ? i * width / Math.max(1, points.length - 1)
                                    : (pointTime - firstTime) * width / (lastTime - firstTime)
                                var y = 8 + (maximum - Number(value)) * (height - 16) / (maximum - minimum)
                                if (!started) { ctx.moveTo(x, y); started = true } else ctx.lineTo(x, y)
                            }
                            ctx.stroke()
                        }
                    }
                }
                Text {
                    visible: (walletController.lendingData.history.points || []).length < 2
                    anchors.horizontalCenter: parent.horizontalCenter; y: 103
                    text: "History begins after the next reliable refresh"
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
                }
                Item {
                    id: dateLabels; x: 16; y: 188; width: parent.width - 32; height: 18
                    property var points: walletController.lendingData.history.points || []
                    property string periodStart: walletController.lendingData.history.periodStart || ""
                    property string periodEnd: walletController.lendingData.history.periodEnd || ""
                    Repeater {
                        model: dateLabels.points
                        delegate: Text {
                            required property var modelData
                            required property int index
                            width: 62; height: 18
                            property double firstTime: Date.parse(dateLabels.periodStart)
                            property double lastTime: Date.parse(dateLabels.periodEnd)
                            property double pointTime: Date.parse(modelData.observedAt)
                            x: isNaN(firstTime) || isNaN(lastTime) || isNaN(pointTime)
                                || lastTime <= firstTime
                                ? index * Math.max(0, dateLabels.width - width)
                                    / Math.max(1, dateLabels.points.length - 1)
                                : Math.max(0, Math.min(dateLabels.width - width,
                                    (pointTime - firstTime) * (dateLabels.width - width)
                                    / (lastTime - firstTime)))
                            text: modelData.label
                            color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }
                }
                Row {
                    x: 16; y: 226; spacing: 18
                    Repeater {
                        model: root.chartMode === "position"
                            ? [{label: "Position", color: "#84C7BA"}, {label: "Earnings", color: "#D5AA64"}]
                            : [{label: "Aave", color: "#B4A1FF"}, {label: "Compound", color: "#00D395"}, {label: "Morpho", color: "#6C8CFF"}]
                        delegate: Row {
                            required property var modelData; spacing: 6
                            Rectangle { width: 9; height: 9; radius: 5; color: modelData.color }
                            Text { text: modelData.label; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10 }
                        }
                    }
                }
            }

            Text {
                id: noActiveProtocols
                x: 28; y: 518; width: 458
                visible: root.showLending && root.primaryProtocols.length === 0 && root.hiddenProtocolCount > 0
                text: "No active Lending positions"
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
            }

            Rectangle {
                id: protocolToggle; objectName: "lendingProtocolToggle"
                x: 28; y: noActiveProtocols.visible ? 546 : 518
                width: 458; height: 40; radius: 12
                visible: root.showLending && root.hiddenProtocolCount > 0
                color: protocolToggleMouse.containsMouse ? Design.surfaceSecondary : "transparent"
                border.width: 1; border.color: Design.border
                function trigger() { root.showAllProtocols = !root.showAllProtocols }
                Text {
                    anchors.centerIn: parent
                    text: root.showAllProtocols ? "Hide empty protocols"
                        : "Show all protocols (" + root.hiddenProtocolCount + ")"
                    color: protocolToggleMouse.containsMouse ? Design.accent : Design.textMuted
                    font.family: Design.fontFamily; font.pixelSize: 12; font.weight: Font.Medium
                }
                MouseArea {
                    id: protocolToggleMouse; anchors.fill: parent
                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: parent.trigger()
                }
            }

            Column {
                id: protocolColumn; objectName: "lendingProtocolColumn"
                visible: root.showLending
                x: 28
                y: protocolToggle.visible ? protocolToggle.y + protocolToggle.height + 14 : 518
                width: 458; spacing: 12
                Repeater {
                    model: root.protocolModel()
                    delegate: SurfaceCard {
                        required property var modelData
                        objectName: "lendingProtocolCard-" + modelData.protocol
                        width: protocolColumn.width; height: 194
                        Image {
                            x: 16; y: 16; width: 24; height: 24
                            source: modelData.logo; fillMode: Image.PreserveAspectFit
                            horizontalAlignment: Image.AlignLeft
                        }
                        Text {
                            x: 48; y: 15; width: 220; text: modelData.name
                            color: Design.text; font.family: Design.fontFamily
                            font.pixelSize: 16; font.weight: Font.DemiBold; elide: Text.ElideRight
                        }
                        Rectangle {
                            x: 48; y: 40; width: 116; height: 20; radius: 8
                            color: Design.surfaceSecondary; border.width: 1; border.color: Design.border
                            Text {
                                anchors.centerIn: parent; text: "Lending Protocol"
                                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 9
                            }
                        }
                        Text {
                            anchors.right: parent.right; anchors.rightMargin: 16; y: 18
                            text: modelData.dataState
                            color: modelData.dataState === "LIVE" ? Design.accent
                                : modelData.dataState === "UNAVAILABLE" ? Design.danger : Design.warning
                            font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
                        }
                        Rectangle { x: 16; y: 72; width: parent.width - 32; height: 1; color: "#12FFFFFF" }
                        Text { x: 16; y: 86; text: "Position"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11 }
                        Text {
                            x: 16; y: 105; width: 190
                            text: walletController.balancesVisible ? modelData.position : "••••"
                            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
                        }
                        Text { x: 16; y: 130; text: walletController.balancesVisible ? modelData.positionUsd : "••••"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
                        Text { x: 226; y: 86; text: "Confirmed annual total"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11 }
                        Text { x: 226; y: 105; text: modelData.confirmedTotal; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold }
                        Text { x: 226; y: 130; text: "Supply " + modelData.baseYield + " · Bonus " + modelData.incentives; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10 }
                        Rectangle { x: 16; y: 151; width: parent.width - 32; height: 1; color: "#12FFFFFF" }
                        Text { x: 16; y: 166; text: "Tracked earnings"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10 }
                        Text { x: 118; y: 164; text: walletController.balancesVisible ? modelData.earnings : "••••"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
                        Text { anchors.right: parent.right; anchors.rightMargin: 16; y: 164; text: modelData.observedAt ? modelData.observedAt.slice(11, 16) + " UTC" : "No current data"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10 }
                    }
                }
            }

            Column {
                id: vaultColumn; objectName: "earnVaultColumn"
                visible: root.showVaults
                x: 28
                y: root.showLending ? protocolColumn.y + protocolColumn.height + 26 : 44
                width: 458; spacing: 12
                Repeater {
                    model: root.vaultProducts
                    delegate: SurfaceCard {
                        required property var modelData
                        objectName: "earnVaultCard-" + modelData.productId
                        width: vaultColumn.width; height: 188
                        Image {
                            x: 16; y: 16; width: 24; height: 24
                            visible: (modelData.logoSource || "").length > 0
                            source: modelData.logoSource || ""; fillMode: Image.PreserveAspectFit
                        }
                        Text {
                            x: (modelData.logoSource || "").length > 0 ? 48 : 16; y: 15; width: 268; text: modelData.displayName
                            color: Design.text; font.family: Design.fontFamily
                            font.pixelSize: 16; font.weight: Font.DemiBold; elide: Text.ElideRight
                        }
                        Text {
                            anchors.right: parent.right; anchors.rightMargin: 16; y: 19
                            text: modelData.dataState
                            color: modelData.dataState === "LIVE" ? Design.accent
                                : modelData.dataState === "UNAVAILABLE" ? Design.danger : Design.warning
                            font.family: Design.fontFamily; font.pixelSize: 11
                        }
                        Rectangle {
                            x: (modelData.logoSource || "").length > 0 ? 48 : 16; y: 40; width: 192; height: 20; radius: 8
                            color: Design.surfaceSecondary; border.width: 1; border.color: Design.border
                            Text {
                                anchors.centerIn: parent; text: modelData.badge || "Vault"
                                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 9
                            }
                        }
                        Rectangle { x: 16; y: 72; width: parent.width - 32; height: 1; color: "#12FFFFFF" }
                        Text { x: 16; y: 87; text: "Position"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10 }
                        Text { x: 16; y: 106; text: walletController.balancesVisible ? modelData.position : "••••"; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 16; font.weight: Font.DemiBold }
                        Text { x: 220; y: 87; text: modelData.metricLabel; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10 }
                        Text { x: 220; y: 106; text: modelData.metricValue; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 16; font.weight: Font.DemiBold }
                        Text { x: 16; y: 143; text: "Risk: " + modelData.riskState; color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 10 }
                        Text { x: 16; y: 162; width: parent.width - 32; text: modelData.exitConstraints; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9; elide: Text.ElideRight }
                    }
                }
                Text {
                    visible: root.vaultProducts.length === 0
                    width: parent.width; height: 42; text: "Vault provider data is unavailable"
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                }
            }

            Text {
                visible: root.showLending
                x: 28
                y: root.showVaults
                    ? vaultColumn.y + vaultColumn.height + 26
                    : protocolColumn.y + protocolColumn.height + 26
                width: 458
                text: "To supply or withdraw, ask Hermes. Wallet will open the prepared transaction for confirmation."
                color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
                wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
            }
        }
    }
}
