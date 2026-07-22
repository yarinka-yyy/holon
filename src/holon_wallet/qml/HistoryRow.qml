import QtQuick
import "."

Item {
    id: root
    property var record: ({})
    property bool showDateHeader: false
    signal detailsRequested(string actionId)
    height: (showDateHeader ? 32 : 0) + 82
    Text {
        visible: root.showDateHeader; x: 2; y: 0
        text: root.record.dateLabel || ""; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
    }
    SurfaceCard {
        y: root.showDateHeader ? 32 : 0; width: parent.width; height: 82
        interactive: true; onTriggered: root.detailsRequested(root.record.actionId || "")
        Image {
            x: 14; anchors.verticalCenter: parent.verticalCenter; width: 44; height: 44
            source: root.record.token === "ETH" ? "assets/ethereum-coin.svg" : "assets/usdc.svg"
            sourceSize: Qt.size(88, 88)
        }
        Text {
            x: 72; y: 15; text: "Sent " + (root.record.token || "")
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 15; font.weight: Font.Medium
        }
        Text {
            x: 72; y: 43; text: "To " + (root.record.shortRecipient || "")
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        }
        Text {
            anchors.right: parent.right; anchors.rightMargin: 18; y: 14
            text: "−" + (root.record.amount || "")
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 14
            font.weight: Font.Medium
        }
        Row {
            anchors.right: parent.right; anchors.rightMargin: 18; y: 43; spacing: 8
            Rectangle {
                visible: root.record.simulated === true
                width: 66; height: 20; radius: 8; color: Design.accentSoft
                Text {
                    anchors.centerIn: parent; text: "SIMULATED"; color: Design.accent
                    font.family: Design.fontFamily; font.pixelSize: 9; font.weight: Font.DemiBold
                }
            }
            Text {
                text: root.record.statusLabel || ""; color:
                    root.record.status === "confirmed" ? Design.accent
                    : root.record.status === "failed" ? Design.danger
                    : root.record.status === "unknown" ? Design.warning : Design.textMuted
                font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
            }
            Text {
                text: "›"; color: Design.textMuted
                font.family: Design.fontFamily; font.pixelSize: 18
            }
        }
    }
}
