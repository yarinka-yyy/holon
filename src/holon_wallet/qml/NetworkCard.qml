import QtQuick
import "."

Item {
    id: root
    property string label: "Network"
    property string status: "Unavailable"
    property url iconSource
    property bool selected: false
    property bool controlEnabled: true
    signal triggered()
    enabled: controlEnabled

    function trigger() {
        if (root.controlEnabled)
            root.triggered()
    }

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: mouse.containsMouse ? Design.surfaceHover
            : root.selected ? "#171735" : Design.surface
        border.width: root.selected ? 1.6 : 1
        border.color: root.selected ? Design.purple : Design.border
        Behavior on border.color { ColorAnimation { duration: Design.normalMotion } }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: -3
        radius: 15
        color: "transparent"
        border.width: root.selected ? 3 : 0
        border.color: "#199A63FF"
        z: -1
    }

    Image {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 12
        width: 27
        height: 27
        source: root.iconSource
        sourceSize: Qt.size(54, 54)
    }

    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 44
        text: root.label
        color: Design.text
        font.family: Design.fontFamily
        font.pixelSize: 12
        font.weight: Font.Medium
    }

    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 62
        text: root.status
        color: root.status === "Live" ? "#55D98A"
            : root.status === "Simulated" ? Design.purpleBright
            : root.selected ? Design.purpleBright : Design.textMuted
        font.family: Design.fontFamily
        font.pixelSize: 10
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
