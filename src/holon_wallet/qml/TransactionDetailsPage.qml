import QtQuick
import "."

PageState {
    id: root
    property var record: walletController.selectedHistoryRecord
    property bool canCheck: (record.status === "pending" || record.status === "unknown")
        && (record.transactionHash || "").length > 0
    property var fallbackRows: [
        {label: "Status", value: record.statusLabel || "Unavailable"},
        {label: record.isRevoke ? "New allowance" : "Amount", value: record.isRevoke ? "0 USDC" : (record.amount || "Unavailable")},
        {label: "Network", value: (record.networkLabel || "") + " · " + (record.chainId || "")},
        {label: "From", value: record.sender || "Unavailable"},
        {label: record.counterpartyLabel || "To", value: record.recipient || "Unavailable"},
        {label: "Contract", value: record.contract || "Unavailable"},
        {label: "Maximum fee", value: record.maxFeeDisplay || "Unavailable"},
        {label: "Actual fee", value: record.actualFeeDisplay || "Unavailable"},
        {label: "Updated", value: record.updatedAt || "Unavailable"}
    ]
    property var detailRows: record.detailRows && record.detailRows.length > 0
        ? record.detailRows : fallbackRows
    property var technicalRows: record.technicalDetails || []

    ScreenHeader {
        objectName: "transactionDetails"; x: 28; y: 54; width: 458
        title: record.isPerpDex ? "Action Details" : "Transaction Details"
        subtitle: record.statusLabel || "Public record"
        onBackRequested: walletController.closeTransactionDetails()
    }

    Flickable {
        id: scroller
        x: 28; y: 148; width: 458; height: 660
        clip: true; contentWidth: width; contentHeight: contentColumn.height
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: contentColumn
            width: scroller.width; spacing: 14

            SurfaceCard {
                width: parent.width
                height: 28 + (root.record.resultExplanation ? explanation.height + 18 : 0)
                    + root.detailRows.length * 48
                Text {
                    id: explanation
                    x: 18; y: 16; width: parent.width - 36
                    visible: !!root.record.resultExplanation
                    text: root.record.resultExplanation || ""
                    color: Design.textMuted; wrapMode: Text.WordWrap
                    font.family: Design.fontFamily; font.pixelSize: 13
                }
                Column {
                    x: 18
                    y: root.record.resultExplanation ? explanation.y + explanation.height + 12 : 14
                    width: parent.width - 36; spacing: 0
                    Repeater {
                        model: root.detailRows
                        delegate: Item {
                            required property var modelData
                            width: parent.width; height: 48
                            Text {
                                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                                width: 142; text: modelData.label; color: Design.textMuted
                                font.family: Design.fontFamily; font.pixelSize: 13
                            }
                            Text {
                                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                width: 260; horizontalAlignment: Text.AlignRight; elide: Text.ElideMiddle
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
                visible: root.technicalRows.length > 0
                width: parent.width; height: visible ? 44 + root.technicalRows.length * 48 : 0
                Text {
                    x: 18; y: 14; text: "Technical details"; color: Design.text
                    font.family: Design.fontFamily; font.pixelSize: 14; font.weight: Font.DemiBold
                }
                Column {
                    x: 18; y: 42; width: parent.width - 36
                    Repeater {
                        model: root.technicalRows
                        delegate: Item {
                            required property var modelData
                            width: parent.width; height: 48
                            Text {
                                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                                width: 150; text: modelData.label; color: Design.textMuted
                                font.family: Design.fontFamily; font.pixelSize: 12
                            }
                            Text {
                                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                width: 250; horizontalAlignment: Text.AlignRight; elide: Text.ElideMiddle
                                text: modelData.value; color: Design.text
                                font.family: Design.fontFamily; font.pixelSize: 12
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                visible: !root.record.isPerpDex
                width: parent.width; height: visible ? 92 : 0
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
                objectName: "detailsCopyDiagnosticsButton"
                visible: root.record.isPerpDex === true
                width: parent.width; height: visible ? 56 : 0
                label: "Copy safe diagnostics"
                onTriggered: walletController.copySelectedHistoryDiagnostics()
            }
            FormButton {
                objectName: "detailsCheckStatusButton"
                visible: root.canCheck; width: parent.width; height: visible ? 56 : 0
                label: walletController.receiptChecking ? "Checking…" : "Check status"
                controlEnabled: !walletController.receiptChecking
                onTriggered: walletController.checkMainnetStatus(root.record.actionId || "")
            }
            Item { width: 1; height: 8 }
        }
    }
    ScrollCue {
        objectName: "transactionDetailsScrollCue"
        anchors.right: scroller.right; anchors.rightMargin: 8
        anchors.bottom: scroller.bottom; anchors.bottomMargin: 8
        suggested: scroller.contentHeight > scroller.height
            && scroller.contentY < scroller.contentHeight - scroller.height - 2
    }
}
