import QtQuick
import "."

PageState {
    id: root
    property bool selectorOpen: false

    Flickable {
        id: scroll
        x: 0; y: 36; width: parent.width; height: parent.height - 42
        contentWidth: width
        contentHeight: 786 + ethRow.height + usdcRow.height - 148
        clip: true; boundsBehavior: Flickable.StopAtBounds

        Item {
            width: scroll.width; height: scroll.contentHeight
            Text {
                x: 28; y: 4; text: "Holon Wallet"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 24; font.weight: Font.DemiBold
            }
            AccountCard {
                id: accountCard; objectName: "accountCard"
                x: 28; y: 54; width: 458; height: 96
                profile: walletController.activeProfile
                onReceiveRequested: walletController.showReceive()
                onCopyRequested: {
                    if (walletController.copyActiveAddress())
                        accountCard.showCopyFeedback()
                }
                onSelectorRequested: root.selectorOpen = !root.selectorOpen
            }
            Row {
                x: 28; y: 178; spacing: 12
                Text {
                    text: "Total Balance"; color: Design.textMuted
                    font.family: Design.fontFamily; font.pixelSize: 15
                }
                Item {
                    objectName: "balanceEyeButton"; width: 24; height: 24
                    function trigger() { walletController.toggleBalancesVisibility() }
                    Image {
                        anchors.fill: parent; source: "assets/eye.svg"; sourceSize: Qt.size(48, 48)
                        opacity: eyeMouse.containsMouse ? 1 : 0.8
                    }
                    MouseArea {
                        id: eyeMouse; anchors.fill: parent; anchors.margins: -6
                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: parent.trigger()
                    }
                }
            }
            Text {
                x: 28; y: 205
                text: walletController.balancesVisible
                    ? walletController.portfolioData.totalUsd : "$ ••••••"
                color: Design.text; font.family: Design.fontFamily
                font.pixelSize: 48; font.weight: Font.Medium; font.letterSpacing: -1.2
            }
            Text {
                anchors.right: parent.right; anchors.rightMargin: 28; y: 224
                text: walletController.publicDataRefreshing
                    ? "Refreshing public data…" : walletController.publicDataUpdatedText
                color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 12
            }

            ActionCard {
                objectName: "sendAction"; x: 28; y: 286; width: 144; height: 102
                label: "Send"; iconSource: "assets/send.svg"
                onTriggered: walletController.showSend()
            }
            ActionCard {
                objectName: "transactionsAction"; x: 185; y: 286; width: 144; height: 102
                label: "History"; iconSource: "assets/clock.svg"
                onTriggered: walletController.showHistory()
            }
            ActionCard {
                objectName: "settingsAction"; x: 342; y: 286; width: 144; height: 102
                label: "Settings"; iconSource: "assets/settings.svg"
                onTriggered: walletController.showSettings()
            }

            Row {
                x: 28; y: 417; spacing: 8
                NetworkCard {
                    objectName: "allNetworksCard"; width: 148; height: 40
                    label: "All Networks"; iconSource: "assets/globe.svg"
                    selected: walletController.selectedNetwork === "all"
                    onTriggered: walletController.selectNetwork("all")
                }
                NetworkCard {
                    objectName: "ethereumNetworkCard"; width: 148; height: 40
                    label: "Ethereum"; iconSource: "assets/ethereum.svg"
                    selected: walletController.selectedNetwork === "ethereum"
                    onTriggered: walletController.selectNetwork("ethereum")
                }
                NetworkCard {
                    objectName: "baseNetworkCard"; width: 146; height: 40
                    label: "Base"; iconSource: "assets/base.png"
                    selected: walletController.selectedNetwork === "base"
                    onTriggered: walletController.selectNetwork("base")
                }
            }

            Text {
                x: 28; y: 486; text: "Assets"; color: Design.textMuted
                font.family: Design.fontFamily; font.pixelSize: 16; font.weight: Font.Medium
            }
            Item {
                objectName: "refreshButton"
                x: 372; y: 474; width: 114; height: 38
                function trigger() { walletController.refreshPublicData() }
                Text {
                    anchors.right: refreshIcon.left; anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter; text: "Refresh"
                    color: refreshMouse.containsMouse ? Design.accent : Design.textMuted
                    font.family: Design.fontFamily; font.pixelSize: 13
                }
                Image {
                    id: refreshIcon; anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 20; height: 20; source: "assets/refresh.svg"
                    sourceSize: Qt.size(40, 40)
                    RotationAnimation on rotation {
                        running: walletController.publicDataRefreshing
                        from: 0; to: 360; duration: 800; loops: Animation.Infinite
                    }
                }
                MouseArea {
                    id: refreshMouse; anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor; onClicked: parent.trigger()
                }
            }
            SurfaceCard {
                x: 28; y: 520; width: 458
                height: ethRow.height + usdcRow.height
                AssetRow {
                    id: ethRow; objectName: "ethAssetRow"
                    width: parent.width; y: 0
                    asset: walletController.portfolioData.assets.length > 0
                        ? walletController.portfolioData.assets[0] : ({})
                    iconSource: "assets/ethereum.svg"
                    amountsVisible: walletController.balancesVisible
                }
                AssetRow {
                    id: usdcRow; objectName: "usdcAssetRow"
                    width: parent.width; y: ethRow.height; divider: false
                    asset: walletController.portfolioData.assets.length > 1
                        ? walletController.portfolioData.assets[1] : ({})
                    iconSource: "assets/usdc.png"
                    amountsVisible: walletController.balancesVisible
                }
            }
            Text {
                x: 28; y: 544 + ethRow.height + usdcRow.height
                text: walletController.publicDataBanner
                color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
            }
        }
    }
    AccountSelector {
        anchors.fill: parent; z: 30; open: root.selectorOpen
        onDismissRequested: root.selectorOpen = false
    }
}
