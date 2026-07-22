import QtQuick
import "."

Item {
    id: root
    property string label: "Label"
    property string value: ""
    readonly property string text: value
    property string secondary: ""
    property url iconSource: "assets/info.svg"
    property url badgeSource: ""
    height: 76
    Rectangle {
        x: 0; anchors.verticalCenter: parent.verticalCenter
        width: 42; height: 42; radius: 21; color: Design.surfaceSecondary
        border.width: 1; border.color: Design.border
        Image {
            anchors.centerIn: parent; width: 22; height: 22
            source: root.iconSource; sourceSize: Qt.size(44, 44)
        }
    }
    Text {
        x: 58; anchors.verticalCenter: parent.verticalCenter
        text: root.label; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 15
    }
    Image {
        visible: root.badgeSource.toString().length > 0
        anchors.right: valueColumn.left; anchors.rightMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        width: 24; height: 24; source: root.badgeSource; sourceSize: Qt.size(48, 48)
    }
    Column {
        id: valueColumn; anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter; spacing: 4
        Text {
            anchors.right: parent.right; text: root.value; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.Medium
        }
        Text {
            visible: root.secondary.length > 0; anchors.right: parent.right
            text: root.secondary; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 13
        }
    }
}
