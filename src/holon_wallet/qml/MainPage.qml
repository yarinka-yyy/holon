import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    property bool selectorOpen: false

    function networkStatus(data) {
        if (walletController.publicDataRefreshing) return "Refreshing"
        if (data.status === "LIVE") return "Live"
        if (data.status === "SIMULATED") return "Simulated"
        return "Unavailable"
    }

    function allNetworkStatus() {
        if (walletController.publicDataRefreshing) return "Refreshing"
        var ethereum = walletController.ethereumData.status
        var base = walletController.baseData.status
        if (ethereum === "LIVE" && base === "LIVE") return "Live"
        if (ethereum === "SIMULATED" && base === "SIMULATED") return "Simulated"
        if (ethereum !== "UNAVAILABLE" || base !== "UNAVAILABLE") return "Partial"
        return "Unavailable"
    }

    Text {
        x: 24; y: 39
        text: "Holon Wallet"
        color: Design.text
        font.family: Design.fontFamily
        font.pixelSize: 25
        font.weight: Font.Bold
    }
    Text {
        x: 24; y: 76
        text: walletController.publicDataBanner
        color: Design.purpleBright
        font.family: Design.fontFamily
        font.pixelSize: 9
        font.letterSpacing: 0.45
    }

    AccountCard {
        id: accountCard
        objectName: "accountCard"
        x: 18; y: 99; width: 478; height: 100
        profile: walletController.activeProfile
        onClicked: root.selectorOpen = !root.selectorOpen
    }

    Text {
        x: 24; y: 215
        text: "Total Balance"
        color: Design.textMuted
        font.family: Design.fontFamily
        font.pixelSize: 14
    }
    Image {
        x: 128; y: 216; width: 18; height: 18
        source: "assets/eye.svg"
        sourceSize: Qt.size(36, 36)
        opacity: 0.8
    }
    Text {
        anchors.right: parent.right; anchors.rightMargin: 24; y: 216
        text: "Data unavailable"
        color: Design.textMuted
        font.family: Design.fontFamily
        font.pixelSize: 11
    }
    Text {
        x: 24; y: 231
        text: "$ —"
        color: Design.text
        font.family: Design.fontFamily
        font.pixelSize: 48
        font.weight: Font.Light
    }
    GlowWave { x: 198; y: 235; width: 316; height: 55 }

    ActionCard {
        objectName: "sendAction"
        x: 24; y: 286; width: 146; height: 78
        label: "Send"; iconSource: "assets/send.svg"; controlEnabled: true
        onTriggered: walletController.showSend()
    }
    ActionCard {
        objectName: "transactionsAction"
        x: 184; y: 286; width: 146; height: 78
        label: "Transactions"; iconSource: "assets/clock.svg"; controlEnabled: true
        onTriggered: walletController.showHistory()
    }
    ActionCard {
        objectName: "settingsAction"
        x: 344; y: 286; width: 146; height: 78
        label: "Settings"; iconSource: "assets/settings.svg"; controlEnabled: true
        onTriggered: walletController.showWallets()
    }

    Text {
        x: 24; y: 378; text: "Networks"
        color: Design.textMuted; font.family: Design.fontFamily
        font.pixelSize: 14; font.weight: Font.DemiBold
    }
    NetworkCard {
        objectName: "allNetworksCard"
        x: 24; y: 403; width: 146; height: 80
        label: "All Networks"; status: root.allNetworkStatus()
        iconSource: "assets/globe.svg"
        selected: walletController.selectedNetwork === "all"
        onTriggered: walletController.selectNetwork("all")
    }
    NetworkCard {
        objectName: "ethereumNetworkCard"
        x: 184; y: 403; width: 146; height: 80
        label: "Ethereum"; status: root.networkStatus(walletController.ethereumData)
        iconSource: "assets/ethereum.svg"
        selected: walletController.selectedNetwork === "ethereum"
        onTriggered: walletController.selectNetwork("ethereum")
    }
    NetworkCard {
        objectName: "baseNetworkCard"
        x: 344; y: 403; width: 146; height: 80
        label: "Base"; status: root.networkStatus(walletController.baseData)
        iconSource: "assets/base.svg"
        selected: walletController.selectedNetwork === "base"
        onTriggered: walletController.selectNetwork("base")
    }

    Text {
        x: 24; y: 498; text: "Assets"
        color: Design.textMuted; font.family: Design.fontFamily
        font.pixelSize: 14; font.weight: Font.DemiBold
    }
    Rectangle {
        x: 18; y: 524; width: 478; height: 110; radius: 13
        color: Design.surface; border.width: 1; border.color: Design.border
        AssetRow {
            objectName: "ethAssetRow"
            width: parent.width; height: 55
            assetName: "Ethereum"; symbol: "ETH"
            iconSource: "assets/ethereum-coin.svg"
            selectedNetwork: walletController.selectedNetwork
            ethereumValue: walletController.ethereumData.ethValue
            ethereumStatus: walletController.ethereumData.status
            baseValue: walletController.baseData.ethValue
            baseStatus: walletController.baseData.status
        }
        AssetRow {
            objectName: "usdcAssetRow"
            y: 55; width: parent.width; height: 55; divider: false
            assetName: "USD Coin"; symbol: "USDC"
            iconSource: "assets/usdc.svg"
            selectedNetwork: walletController.selectedNetwork
            ethereumValue: walletController.ethereumData.usdcValue
            ethereumStatus: walletController.ethereumData.status
            baseValue: walletController.baseData.usdcValue
            baseStatus: walletController.baseData.status
        }
    }
    Text {
        x: 24; y: 655; text: "Encrypted local Account"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
    }
    Item {
        id: refreshControl
        objectName: "refreshButton"
        x: 326; y: 641; width: 164; height: 31
        function trigger() { walletController.refreshPublicData() }
        Text {
            anchors.right: refreshIcon.left; anchors.rightMargin: 7
            anchors.verticalCenter: parent.verticalCenter
            text: walletController.publicDataUpdatedText
            color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
        }
        Image {
            id: refreshIcon
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            width: 19; height: 19; source: "assets/refresh.svg"
            sourceSize: Qt.size(38, 38)
            opacity: refreshMouse.containsMouse ? 1 : 0.8
            RotationAnimation on rotation {
                running: walletController.publicDataRefreshing
                from: 0; to: 360; duration: 800; loops: Animation.Infinite
            }
        }
        MouseArea {
            id: refreshMouse; anchors.fill: parent; hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: refreshControl.trigger()
        }
    }

    AccountSelector {
        anchors.fill: parent; z: 30; open: root.selectorOpen
        onDismissRequested: root.selectorOpen = false
    }
}
