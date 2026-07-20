import QtQuick
import "."

Item {
    id: root
    property string label: "Action"
    property url iconSource
    property bool controlEnabled: false
    property string badge: ""
    signal triggered()
    enabled: root.controlEnabled

    function trigger() {
        if (root.controlEnabled)
            root.triggered()
    }

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: mouse.containsMouse && root.controlEnabled
            ? Design.surfaceHover : Design.surface
        border.width: 1
        border.color: mouse.containsMouse && root.controlEnabled
            ? Design.purple : Design.border
        Behavior on color { ColorAnimation { duration: Design.fastMotion } }
        Behavior on border.color { ColorAnimation { duration: Design.fastMotion } }
    }

    Image {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 13
        width: 27
        height: 27
        source: root.iconSource
        sourceSize: Qt.size(54, 54)
        opacity: root.controlEnabled ? 1 : 0.62
    }

    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 49
        text: root.label
        color: root.controlEnabled ? Design.text : Design.textMuted
        font.family: Design.fontFamily
        font.pixelSize: 13
    }

    Rectangle {
        anchors.top: parent.top; anchors.right: parent.right
        anchors.topMargin: 8; anchors.rightMargin: 8
        width: 31; height: 15; radius: 5
        visible: root.badge.length > 0
        color: "#34206A"; border.width: 1; border.color: Design.purple
        Text {
            anchors.centerIn: parent; text: root.badge
            color: Design.purpleBright; font.family: Design.fontFamily
            font.pixelSize: 7; font.weight: Font.Bold; font.letterSpacing: 0.4
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        enabled: root.controlEnabled
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.trigger()
    }

    scale: mouse.pressed ? 0.975 : 1
    Behavior on scale { NumberAnimation { duration: Design.fastMotion } }
}
