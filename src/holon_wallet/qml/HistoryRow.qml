import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    property var record: ({})
    property bool showDateHeader: false
    property bool canCheck: record.simulated !== true
        && (record.status === "pending" || record.status === "unknown")
        && (record.transactionHash || "").length > 0
    height: (showDateHeader ? 112 : 86) + (canCheck ? 18 : 0)

    Text {
        visible: root.showDateHeader
        x: 4; y: 0
        text: root.record.dateLabel || ""
        color: Design.textMuted; font.family: Design.fontFamily
        font.pixelSize: 12; font.weight: Font.Medium
    }

    Rectangle {
        id: card
        x: 0; y: root.showDateHeader ? 26 : 0
        width: parent.width; height: root.canCheck ? 100 : 82; radius: 13
        color: Design.surface
        border.width: 1; border.color: Design.border
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "#0E172C" }
            GradientStop { position: 1.0; color: "#080F20" }
        }

        Rectangle {
            x: 14; anchors.verticalCenter: parent.verticalCenter
            width: 48; height: 48; radius: 24
            gradient: Gradient {
                GradientStop { position: 0.0; color: root.record.token === "USDC" ? "#31A5FF" : "#7D8DFF" }
                GradientStop { position: 1.0; color: root.record.token === "USDC" ? "#1260EA" : "#3A42D7" }
            }
            Image {
                anchors.centerIn: parent; width: 31; height: 31
                source: root.record.token === "USDC"
                    ? "assets/usdc.svg" : "assets/ethereum.svg"
                sourceSize: Qt.size(62, 62)
            }
        }

        Text {
            x: 76; y: 15; text: "Sent"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 16; font.weight: Font.DemiBold
        }
        Text {
            x: 76; y: 43
            text: "To: " + (root.record.shortRecipient || "")
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }

        Text {
            anchors.right: parent.right; anchors.rightMargin: 16; y: 14
            text: "−" + (root.record.amount || "")
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 14
            font.weight: Font.Medium
        }
        Row {
            anchors.right: parent.right; anchors.rightMargin: 16; y: 43; spacing: 6
            Rectangle {
                visible: root.record.simulated === true
                width: 51; height: 17; radius: 5
                color: "#34206A"; border.width: 1; border.color: Design.purple
                Text {
                    anchors.centerIn: parent; text: "SIMULATED"
                    color: Design.purpleBright; font.family: Design.fontFamily
                    font.pixelSize: 7; font.weight: Font.Bold
                }
            }
            Text {
                text: (root.record.networkLabel || "") + "  ·  " + (root.record.statusLabel || "")
                color: root.record.status === "confirmed" ? "#55D98A"
                    : root.record.status === "failed" ? "#FF7D91" : Design.textMuted
                font.family: Design.fontFamily; font.pixelSize: 10
            }
        }
        Item {
            objectName: "historyCheckStatusButton"
            visible: root.canCheck
            anchors.right: parent.right; anchors.rightMargin: 14; y: 68
            width: 98; height: 23
            enabled: !walletController.receiptChecking
            function trigger() {
                if (enabled)
                    walletController.checkMainnetStatus(root.record.actionId || "")
            }
            Rectangle {
                anchors.fill: parent; radius: 7
                color: checkMouse.containsMouse ? Design.surfaceHover : Design.surfaceRaised
                border.width: 1; border.color: Design.border
            }
            Text {
                anchors.centerIn: parent
                text: walletController.receiptChecking ? "Checking…" : "Check status"
                color: Design.purpleBright; font.family: Design.fontFamily; font.pixelSize: 8
            }
            MouseArea {
                id: checkMouse; anchors.fill: parent; hoverEnabled: true
                enabled: parent.enabled; cursorShape: Qt.PointingHandCursor
                onClicked: parent.trigger()
            }
        }
    }
}
