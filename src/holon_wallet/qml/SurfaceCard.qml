import QtQuick
import "."

Rectangle {
    id: root
    property bool selected: false
    property bool interactive: false
    property bool hovered: mouse.containsMouse
    signal triggered()
    radius: Design.cardRadius
    color: hovered && interactive ? Design.surfaceHover : Design.surfaceCard
    border.width: selected ? 1.5 : 1
    border.color: selected ? Design.accent : hovered && interactive
        ? "#7384C7BA" : "#14FFFFFF"
    Behavior on color { ColorAnimation { duration: Design.fastMotion } }
    Behavior on border.color { ColorAnimation { duration: Design.fastMotion } }
    MouseArea {
        id: mouse; anchors.fill: parent; enabled: root.interactive
        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
        onClicked: root.triggered()
    }
    scale: mouse.pressed ? 0.985 : 1
    Behavior on scale { NumberAnimation { duration: 120 } }
}
