import QtQuick
import "."

Item {
    id: root
    property var asset: ({})
    property url iconSource
    property bool divider: true
    property bool amountsVisible: true
    property bool expanded: false
    height: 74 + (expanded ? 54 : 0)

    Image {
        x: 14; y: 17; width: 40; height: 40
        source: root.iconSource; sourceSize: Qt.size(80, 80)
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
            text: root.amountsVisible ? (root.asset.amount || "Data unavailable") : "••••••"
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 15
            font.weight: Font.Medium
        }
        Text {
            anchors.right: parent.right
            text: root.amountsVisible ? (root.asset.usd || "Data unavailable") : "••••••"
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
