import QtQuick
import "."

Item {
    id: root
    property string label: "Action"
    property url iconSource
    property bool controlEnabled: false
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
