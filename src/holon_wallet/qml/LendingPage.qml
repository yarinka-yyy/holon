import QtQuick
import "."

PageState {
    id: root
    property string chartMode: "position"
    property bool showAllProtocols: false
    property var primaryProtocols: walletController.lendingData.visibleProtocols || []
    property var emptyProtocols: walletController.lendingData.emptyProtocols || []
    property int hiddenProtocolCount: walletController.lendingData.hiddenProtocolCount || 0
    function protocolModel() {
        return showAllProtocols ? primaryProtocols.concat(emptyProtocols) : primaryProtocols
    }
    onActiveChanged: if (active) showAllProtocols = false

    ScreenHeader {
        objectName: "lendingHeader"; x: 28; y: 42; width: parent.width - 56
        title: "Lending"; subtitle: "Base USDC positions and yield"
        onBackRequested: walletController.showMain()
    }

    Flickable {
        id: scroll
        x: 0; y: 120; width: parent.width; height: parent.height - 126
        contentWidth: width; contentHeight: content.height + 28
        clip: true; boundsBehavior: Flickable.StopAtBounds

        Item {
            id: content; width: scroll.width
            height: protocolColumn.y + protocolColumn.height + 86

            Item {
                objectName: "lendingRefreshButton"
                anchors.right: parent.right; anchors.rightMargin: 28
                y: 0; width: 126; height: 34
                function trigger() { walletController.refreshLendingData(true) }
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
                        running: walletController.lendingDataRefreshing
                        from: 0; to: 360; duration: 800; loops: Animation.Infinite
                    }
                }
                MouseArea {
                    id: refreshMouse; anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor; onClicked: parent.trigger()
                }
            }

            Text {
                x: 28; y: 9; text: walletController.lendingData.updatedText
                color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 12
            }

            SurfaceCard {
                objectName: "lendingSummaryCard"
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
                id: chartModes; x: 28; y: 194; spacing: 8
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
                visible: root.primaryProtocols.length === 0 && root.hiddenProtocolCount > 0
                text: "No active Lending positions"
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
            }

            Rectangle {
                id: protocolToggle; objectName: "lendingProtocolToggle"
                x: 28; y: noActiveProtocols.visible ? 546 : 518
                width: 458; height: 40; radius: 12
                visible: root.hiddenProtocolCount > 0
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
                            x: 16; y: 15; width: 88; height: 30
                            source: modelData.logo; fillMode: Image.PreserveAspectFit
                            horizontalAlignment: Image.AlignLeft
                        }
                        Text {
                            anchors.right: parent.right; anchors.rightMargin: 16; y: 18
                            text: modelData.dataState
                            color: modelData.dataState === "LIVE" ? Design.accent
                                : modelData.dataState === "UNAVAILABLE" ? Design.danger : Design.warning
                            font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
                        }
                        Rectangle { x: 16; y: 57; width: parent.width - 32; height: 1; color: "#12FFFFFF" }
                        Text { x: 16; y: 72; text: "Position"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11 }
                        Text {
                            x: 16; y: 91; width: 190
                            text: walletController.balancesVisible ? modelData.position : "••••"
                            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
                        }
                        Text { x: 16; y: 116; text: walletController.balancesVisible ? modelData.positionUsd : "••••"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
                        Text { x: 226; y: 72; text: "Confirmed annual total"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11 }
                        Text { x: 226; y: 91; text: modelData.confirmedTotal; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold }
                        Text { x: 226; y: 116; text: "Base " + modelData.baseYield + " · Bonus " + modelData.incentives; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10 }
                        Rectangle { x: 16; y: 142; width: parent.width - 32; height: 1; color: "#12FFFFFF" }
                        Text { x: 16; y: 158; text: "Tracked earnings"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10 }
                        Text { x: 118; y: 156; text: walletController.balancesVisible ? modelData.earnings : "••••"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
                        Text { anchors.right: parent.right; anchors.rightMargin: 16; y: 156; text: modelData.observedAt ? modelData.observedAt.slice(11, 16) + " UTC" : "No current data"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10 }
                    }
                }
            }

            Text {
                x: 28; y: protocolColumn.y + protocolColumn.height + 26; width: 458
                text: "To supply or withdraw, ask Hermes. Wallet will open the prepared transaction for confirmation."
                color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
                wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
            }
        }
    }
}
