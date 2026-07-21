import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    property bool selectorOpen: false

    BackButton {
        objectName: "historyBackButton"; x: 22; y: 42
        onTriggered: walletController.showMain()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 49
        text: "History"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Text {
        x: 24; y: 99
        text: "WALLET-INITIATED HISTORY  ·  PUBLIC DATA"
        color: Design.purpleBright; font.family: Design.fontFamily
        font.pixelSize: 9; font.letterSpacing: 0.42
    }

    AccountCard {
        objectName: "historyAccountCard"
        x: 18; y: 119; width: 478; height: 88
        profile: walletController.activeProfile
        onClicked: root.selectorOpen = !root.selectorOpen
    }

    ListView {
        id: historyList
        objectName: "historyList"
        x: 18; y: 223; width: 478; height: 407
        clip: true; spacing: 8
        model: walletController.historyRecords
        delegate: HistoryRow {
            required property var modelData
            objectName: "historyRow_" + modelData.actionId
            width: historyList.width
            record: modelData
            showDateHeader: modelData.showDateHeader
        }
    }

    Column {
        visible: walletController.historyStateLabel.length > 0
        anchors.horizontalCenter: parent.horizontalCenter
        y: 340; spacing: 13
        Image {
            anchors.horizontalCenter: parent.horizontalCenter
            width: 34; height: 34; source: "assets/clock.svg"
            sourceSize: Qt.size(68, 68); opacity: 0.72
        }
        Text {
            objectName: "historyStateLabel"
            anchors.horizontalCenter: parent.horizontalCenter
            text: walletController.historyStateLabel
            color: walletController.historyAvailable ? Design.textMuted : "#FF9AA9"
            font.family: Design.fontFamily; font.pixelSize: 12
        }
        Text {
            visible: walletController.historyAvailable
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Only actions started from Holon Wallet appear here"
            color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
        }
    }

    Text {
        x: 24; y: 650; text: "Last 500 records  ·  Stored locally"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
    }

    AccountSelector {
        anchors.fill: parent; z: 30; open: root.selectorOpen
        onDismissRequested: root.selectorOpen = false
    }
}
