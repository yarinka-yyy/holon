import QtQuick
import "."

Item {
    id: root
    property string label: "Continue"
    property bool primary: true
    property bool controlEnabled: true
    property url iconSource: ""
    signal triggered()
    enabled: controlEnabled
    opacity: controlEnabled ? 1 : 0.44

    function trigger() { if (controlEnabled) triggered() }

    Rectangle {
        anchors.fill: parent
        radius: Design.controlRadius
        color: root.primary
            ? (mouse.pressed ? Design.accentPressed
                : mouse.containsMouse ? Design.accentHover : Design.accent)
            : (mouse.containsMouse ? Design.surfaceHover : Design.surface)
        border.width: 1
        border.color: root.primary ? "#5FA99B" : Design.border
        Behavior on color { ColorAnimation { duration: Design.fastMotion } }
        Behavior on border.color { ColorAnimation { duration: Design.fastMotion } }
    }
    Row {
        anchors.centerIn: parent
        spacing: 10
        Image {
            visible: root.iconSource.toString().length > 0
            width: 22; height: 22; source: root.iconSource
            sourceSize: Qt.size(44, 44)
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.label
            color: root.primary ? Design.textOnAccent : Design.text
            font.family: Design.fontFamily; font.pixelSize: 16; font.weight: Font.Medium
        }
    }
    MouseArea {
        id: mouse; anchors.fill: parent; enabled: root.controlEnabled
        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
        onClicked: root.trigger()
    }
    scale: mouse.pressed ? 0.98 : 1
    Behavior on scale { NumberAnimation { duration: 120 } }
}
