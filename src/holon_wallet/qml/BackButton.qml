import QtQuick
import "."

Rectangle {
    id: root
    signal triggered()
    width: 48; height: 48; radius: 11
    color: mouse.containsMouse ? Design.surfaceHover : Design.surface
    border.width: 1; border.color: Design.border
    function trigger() { root.triggered() }
    Behavior on color { ColorAnimation { duration: Design.fastMotion } }
    Image {
        anchors.centerIn: parent; width: 23; height: 23
        source: "assets/back.svg"; sourceSize: Qt.size(46, 46)
    }
    MouseArea {
        id: mouse; anchors.fill: parent; hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.trigger()
    }
}
