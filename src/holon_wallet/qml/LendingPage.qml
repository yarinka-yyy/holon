import QtQuick
import "."

PageState {
    id: root
    property string chartMode: "position"

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
                x: 28; y: 242; width: 458; height: 236
                Text {
                    x: 16; y: 13
                    text: root.chartMode === "position" ? "Position and earnings · USDC" : "Confirmed annual yield · %"
                    color: Design.text; font.family: Design.fontFamily; font.pixelSize: 13
                    font.weight: Font.Medium
                }
                Canvas {
                    id: chart; objectName: "lendingHistoryChart"
                    x: 16; y: 45; width: parent.width - 32; height: 145
                    property var points: walletController.lendingData.history.points || []
                    onPointsChanged: requestPaint()
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
                                var x = i * width / Math.max(1, points.length - 1)
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
                Row {
                    x: 16; y: 202; spacing: 18
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

            Column {
                id: protocolColumn; objectName: "lendingProtocolColumn"
                x: 28; y: 496; width: 458; spacing: 12
                Repeater {
                    model: walletController.lendingData.protocols || []
                    delegate: SurfaceCard {
                        required property var modelData
                        required property int index
                        objectName: index === 0 ? "lendingProtocolCard-aave-v3"
                            : index === 1 ? "lendingProtocolCard-compound-v3"
                            : "lendingProtocolCard-morpho-v1"
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
