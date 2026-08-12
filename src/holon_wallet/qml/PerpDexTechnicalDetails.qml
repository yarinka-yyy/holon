import QtQuick
import "."

Item {
    id: root
    property var details: []
    property bool expanded: false
    readonly property real expandedHeight: 78 + detailColumn.implicitHeight
    width: 458
    height: expanded ? expandedHeight : 48

    SurfaceCard {
        width: parent.width; height: 48; interactive: true
        onTriggered: root.expanded = !root.expanded
        Text {
            x: 16; anchors.verticalCenter: parent.verticalCenter
            text: "Technical details"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
        }
        Text {
            anchors.right: parent.right; anchors.rightMargin: 18; anchors.verticalCenter: parent.verticalCenter
            text: root.expanded ? "−" : "+"; color: Design.accent
            font.family: Design.fontFamily; font.pixelSize: 20
        }
    }
    SurfaceCard {
        visible: root.expanded; y: 58; width: parent.width
        height: detailColumn.implicitHeight + 20
        Column {
            id: detailColumn; x: 16; y: 10; width: parent.width - 32; spacing: 8
            Repeater {
                model: root.details || []
                delegate: Item {
                    required property var modelData
                    width: detailColumn.width; height: valueText.implicitHeight + 14
                    Text {
                        x: 0; y: 0; width: 124; text: modelData.label || ""
                        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
                    }
                    Text {
                        id: valueText; x: 132; y: 0; width: parent.width - 132
                        text: modelData.value || ""; elide: Text.ElideMiddle; wrapMode: Text.WrapAnywhere
                        horizontalAlignment: Text.AlignRight; color: Design.textMuted
                        font.family: Design.fontFamily; font.pixelSize: 10
                    }
                }
            }
        }
    }
}
