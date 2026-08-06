import QtQuick
import "."

Item {
    id: root
    property string label: "Continue"
    property bool primary: true
    property bool controlEnabled: true
    signal triggered()
    enabled: controlEnabled
    opacity: controlEnabled ? 1 : 0.44
    Rectangle {
        anchors.fill: parent; radius: Design.controlRadius
        color: root.primary
            ? (mouse.pressed ? Design.accentPressed : mouse.containsMouse ? Design.accentHover : Design.accent)
            : (mouse.containsMouse ? Design.surfaceHover : Design.surface)
        border.width: 1; border.color: root.primary ? "#5FA99B" : Design.border
    }
    Text {
        anchors.centerIn: parent; text: root.label
        color: root.primary ? Design.textOnAccent : Design.text
        font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
    }
    MouseArea {
        id: mouse; anchors.fill: parent; enabled: root.controlEnabled
        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
        onClicked: root.triggered()
    }
}
