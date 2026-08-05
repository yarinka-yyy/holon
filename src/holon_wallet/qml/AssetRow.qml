import QtQuick
import "."

Item {
    id: root
    property var asset: ({})
    property url iconSource
    property bool divider: true
    property bool amountsVisible: true
    property bool expanded: false
    readonly property url protocolBadgeIcon: ({
        "aave-v3": "assets/aave-mark-white.svg",
        "compound-v3": "assets/compound-mark.svg",
        "morpho-v1": "assets/morpho-mark-white.svg"
    })[String(root.asset.assetId || "")] || ""
    readonly property bool isLendingPosition: protocolBadgeIcon.toString().length > 0
    height: 74 + (expanded ? 54 : 0)

    Image {
        visible: !root.isLendingPosition
        x: 14; y: 17; width: 40; height: 40
        source: root.iconSource; sourceSize: Qt.size(80, 80)
        fillMode: Image.PreserveAspectFit
    }
    Rectangle {
        visible: root.isLendingPosition
        x: 14; y: 17; width: 40; height: 40; radius: 20
        color: Design.surfaceSecondary; border.width: 1; border.color: Design.border
        clip: true
        Image {
            visible: root.protocolBadgeIcon !== ""
            anchors.centerIn: parent; width: 30; height: 30
            source: root.protocolBadgeIcon; sourceSize: Qt.size(60, 60)
            fillMode: Image.PreserveAspectFit
        }
    }
    Text {
        x: 70; y: 15; text: root.asset.label || "Asset"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 16; font.weight: Font.Medium
    }
    Text {
        x: 70; y: 40; text: root.asset.symbol || ""; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 13
    }
    Column {
        anchors.right: parent.right; anchors.rightMargin: 34; y: 14; spacing: 3
        Text {
            anchors.right: parent.right
            text: root.amountsVisible ? (root.asset.usd || "Data unavailable") : "••••••"
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 15
            font.weight: Font.Medium
        }
        Text {
            anchors.right: parent.right
            text: root.amountsVisible
                ? ((root.asset.amount || "Data unavailable") + (root.asset.incomplete ? " · partial" : ""))
                : "••••••"
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
        }
    }
    Text {
        anchors.right: parent.right; anchors.rightMargin: 14; y: 26
        text: root.asset.breakdown && root.asset.breakdown.length > 1 ? "⌄" : ""
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 18
        rotation: root.expanded ? 180 : 0
        Behavior on rotation { NumberAnimation { duration: Design.fastMotion } }
    }
    Row {
        visible: root.expanded; x: 70; y: 76; spacing: 24
        Repeater {
            model: root.asset.breakdown || []
            delegate: Column {
                required property var modelData
                spacing: 2
                Text {
                    text: modelData.label; color: Design.textFaint
                    font.family: Design.fontFamily; font.pixelSize: 11
                }
                Text {
                    text: root.amountsVisible ? modelData.amount : "••••"
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
                }
            }
        }
    }
    MouseArea {
        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
        enabled: root.asset.breakdown && root.asset.breakdown.length > 1
        onClicked: root.expanded = !root.expanded
    }
    Rectangle {
        visible: root.divider; anchors.left: parent.left; anchors.right: parent.right
        anchors.bottom: parent.bottom; height: 1; color: "#0FFFFFFF"
    }
}
