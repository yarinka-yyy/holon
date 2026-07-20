import QtQuick
import "."

Item {
    id: root
    property var profile: ({})
    property bool interactive: true
    signal clicked()

    function trigger() {
        if (root.interactive)
            root.clicked()
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: -3
        radius: 16
        color: "transparent"
        border.width: 3
        border.color: "#159A63FF"
        z: -1
    }

    Rectangle {
        anchors.fill: parent
        radius: 13
        color: mouse.containsMouse && root.interactive
            ? Design.surfaceHover : Design.surface
        border.width: mouse.containsMouse && root.interactive ? 1.4 : 1
        border.color: mouse.containsMouse && root.interactive
            ? Design.purple : Design.border
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "#121A31" }
            GradientStop { position: 0.55; color: "#0A1123" }
            GradientStop { position: 1.0; color: "#081020" }
        }
        Behavior on border.color { ColorAnimation { duration: Design.normalMotion } }
    }

    Avatar {
        width: 72
        height: 72
        x: 18
        anchors.verticalCenter: parent.verticalCenter
        initials: root.profile.initials || "A1"
        primary: true
    }

    Text {
        id: label
        objectName: "mainAccountLabel"
        x: 108
        y: 27
        text: root.profile.label || "Main Account"
        color: Design.text
        font.family: Design.fontFamily
        font.pixelSize: 20
        font.weight: Font.DemiBold
    }

    Text {
        x: 108
        y: 58
        text: root.profile.shortAddress || ""
        color: Design.textMuted
        font.family: Design.fontFamily
        font.pixelSize: 13
    }

    Image {
        x: 248
        y: 57
        width: 16
        height: 16
        source: "assets/copy.svg"
        sourceSize: Qt.size(32, 32)
        opacity: 0.72
    }

    Image {
        anchors.right: parent.right
        anchors.rightMargin: 22
        anchors.verticalCenter: parent.verticalCenter
        width: 18
        height: 18
        source: "assets/chevron-down.svg"
        sourceSize: Qt.size(36, 36)
        opacity: root.interactive ? 0.9 : 0.45
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        enabled: root.interactive
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.trigger()
    }

    scale: mouse.pressed ? 0.992 : 1
    Behavior on scale { NumberAnimation { duration: Design.fastMotion } }
}
