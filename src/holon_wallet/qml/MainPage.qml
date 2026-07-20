import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    property bool selectorOpen: false

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
        text: "PROTOTYPE  ·  SIMULATED DATA"
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
        label: "Send"; iconSource: "assets/send.svg"; controlEnabled: false
    }
    ActionCard {
        objectName: "transactionsAction"
        x: 184; y: 286; width: 146; height: 78
        label: "Transactions"; iconSource: "assets/clock.svg"; controlEnabled: false
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
        x: 24; y: 403; width: 146; height: 80
        label: "All Networks"; status: "Unavailable"
        iconSource: "assets/globe.svg"; selected: true
    }
    NetworkCard {
        x: 184; y: 403; width: 146; height: 80
        label: "Ethereum"; status: "Unavailable"; iconSource: "assets/ethereum.svg"
    }
    NetworkCard {
        x: 344; y: 403; width: 146; height: 80
        label: "Base"; status: "Unavailable"; iconSource: "assets/base.svg"
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
            width: parent.width; height: 55
            assetName: "Ethereum"; symbol: "ETH"; chain: "Ethereum"
            iconSource: "assets/ethereum-coin.svg"
        }
        AssetRow {
            y: 55; width: parent.width; height: 55; divider: false
            assetName: "USD Coin"; symbol: "USDC"; chain: "Base"
            iconSource: "assets/usdc.svg"
        }
    }
    Text {
        x: 24; y: 655; text: "Prototype data only"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
    }
    Text {
        anchors.right: parent.right; anchors.rightMargin: 24; y: 655
        text: "No RPC connection"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
    }

    AccountSelector {
        anchors.fill: parent; z: 30; open: root.selectorOpen
        onDismissRequested: root.selectorOpen = false
    }
}
