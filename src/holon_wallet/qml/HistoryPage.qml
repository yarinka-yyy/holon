import QtQuick
import "."

PageState {
    id: root
    property bool selectorOpen: false
    ScreenHeader {
        objectName: "history"; x: 28; y: 54; width: 458
        title: "History"; subtitle: "Wallet-initiated transactions"
        onBackRequested: walletController.showMain()
    }
    SurfaceCard {
        objectName: "historyAccountSelector"; x: 28; y: 146; width: 458; height: 70
        interactive: true; onTriggered: root.selectorOpen = true
        Avatar {
            x: 12; anchors.verticalCenter: parent.verticalCenter; width: 44; height: 44
            initials: walletController.activeProfile.initials || "A"; primary: true
        }
        Text {
            x: 70; y: 14; text: walletController.activeProfile.label || "Account"
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.Medium
        }
        Text {
            x: 70; y: 39; text: walletController.activeProfile.shortAddress || ""
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        }
        Image {
            anchors.right: parent.right; anchors.rightMargin: 18
            anchors.verticalCenter: parent.verticalCenter; width: 20; height: 20
            source: "assets/chevron-down.svg"; sourceSize: Qt.size(40, 40)
        }
    }
    Text {
        id: stateLabel; objectName: "historyStateLabel"
        visible: text.length > 0; anchors.centerIn: historyList
        text: walletController.historyStateLabel; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 14
    }
    ListView {
        id: historyList; objectName: "historyList"
        x: 28; y: 244; width: 458; height: 552
        model: walletController.historyRecords; spacing: 12; clip: true
        delegate: HistoryRow {
            required property var modelData
            width: historyList.width; record: modelData
            showDateHeader: modelData.showDateHeader
            onDetailsRequested: actionId => walletController.showTransactionDetails(actionId)
        }
    }
    AccountSelector {
        anchors.fill: parent; z: 30; open: root.selectorOpen
        onDismissRequested: root.selectorOpen = false
    }
}
