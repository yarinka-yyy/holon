import QtQuick
import "."

PageState {
    id: root
    property var record: walletController.selectedHistoryRecord
    property bool canCheck: (record.status === "pending" || record.status === "unknown")
        && (record.transactionHash || "").length > 0
    ScreenHeader {
        objectName: "transactionDetails"; x: 28; y: 54; width: 458
        title: "Transaction Details"; subtitle: record.statusLabel || "Public record"
        onBackRequested: walletController.closeTransactionDetails()
    }
    SurfaceCard {
        x: 28; y: 148; width: 458; height: 476
        Column {
            x: 18; y: 16; width: parent.width - 36; spacing: 0
            Repeater {
                model: [
                    {label: "Status", value: root.record.statusLabel || "Unavailable"},
                    {label: root.record.isRevoke ? "New allowance" : "Amount", value: root.record.isRevoke ? "0 USDC" : (root.record.amount || "Unavailable")},
                    {label: "Network", value: (root.record.networkLabel || "") + " · " + (root.record.chainId || "")},
                    {label: "From", value: root.record.sender || "Unavailable"},
                    {label: root.record.counterpartyLabel || "To", value: root.record.recipient || "Unavailable"},
                    {label: "Contract", value: root.record.contract || "Unavailable"},
                    {label: "Maximum fee", value: root.record.maxFeeDisplay || "Unavailable"},
                    {label: "Actual fee", value: root.record.actualFeeDisplay || "Unavailable"},
                    {label: "Updated", value: root.record.updatedAt || "Unavailable"}
                ]
                delegate: Item {
                    required property var modelData
                    width: parent.width; height: 48
                    Text {
                        anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                        text: modelData.label; color: Design.textMuted
                        font.family: Design.fontFamily; font.pixelSize: 13
                    }
                    Text {
                        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        width: 270; horizontalAlignment: Text.AlignRight; elide: Text.ElideMiddle
                        text: modelData.value; color: Design.text
                        font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
                    }
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.bottom: parent.bottom; height: 1; color: "#0FFFFFFF"
                    }
                }
            }
        }
    }
    SurfaceCard {
        x: 28; y: 642; width: 458; height: 92
        Text {
            x: 16; y: 14; text: "Transaction hash"; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 12
        }
        Text {
            x: 16; y: 42; width: parent.width - 32; elide: Text.ElideMiddle
            text: root.record.transactionHash || "Not available"
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
    FormButton {
        objectName: "detailsCheckStatusButton"; x: 72; y: 752; width: 370; height: 56
        visible: root.canCheck; label: walletController.receiptChecking ? "Checking…" : "Check status"
        controlEnabled: !walletController.receiptChecking
        onTriggered: walletController.checkMainnetStatus(root.record.actionId || "")
    }
}
