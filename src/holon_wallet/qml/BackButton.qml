import QtQuick
import "."

Rectangle {
    id: root
    signal triggered()
    width: 56; height: 56; radius: Design.controlRadius
    color: mouse.containsMouse ? Design.surfaceHover : "#04FFFFFF"
    border.width: 1; border.color: mouse.containsMouse ? "#7384C7BA" : "#14FFFFFF"
    function trigger() { triggered() }
    Behavior on color { ColorAnimation { duration: Design.fastMotion } }
    Behavior on border.color { ColorAnimation { duration: Design.fastMotion } }
    Image {
        anchors.centerIn: parent; width: 24; height: 24
        source: "assets/back.svg"; sourceSize: Qt.size(48, 48)
    }
    MouseArea {
        id: mouse; anchors.fill: parent; hoverEnabled: true
        cursorShape: Qt.PointingHandCursor; onClicked: root.trigger()
    }
    scale: mouse.pressed ? 0.98 : 1
    Behavior on scale { NumberAnimation { duration: 120 } }
}
