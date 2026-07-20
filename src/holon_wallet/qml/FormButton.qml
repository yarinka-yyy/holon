import QtQuick
import "."

Item {
    id: root
    property string label: "Continue"
    property bool primary: true
    property bool controlEnabled: true
    signal triggered()
    enabled: root.controlEnabled

    function trigger() {
        if (root.controlEnabled)
            root.triggered()
    }

    Rectangle {
        anchors.fill: parent
        radius: 12
        opacity: root.controlEnabled ? 1 : 0.48
        border.width: 1
        border.color: root.primary ? "#AA9A63FF" : Design.border
        color: root.primary ? Design.purple : (mouse.containsMouse ? Design.surfaceHover : Design.surface)
        gradient: root.primary ? primaryGradient : null
        Behavior on opacity { NumberAnimation { duration: Design.fastMotion } }
        Behavior on color { ColorAnimation { duration: Design.fastMotion } }
    }
    Gradient {
        id: primaryGradient
        orientation: Gradient.Horizontal
        GradientStop { position: 0; color: mouse.containsMouse ? "#B98BFF" : "#A978FF" }
        GradientStop { position: 0.55; color: "#8657F5" }
        GradientStop { position: 1; color: mouse.containsMouse ? "#7045F2" : "#6035E4" }
    }
    Text {
        anchors.centerIn: parent
        text: root.label
        color: root.primary ? "#FFFFFF" : Design.purpleBright
        font.family: Design.fontFamily
        font.pixelSize: 16
        font.weight: Font.Medium
    }
    MouseArea {
        id: mouse
        anchors.fill: parent
        enabled: root.controlEnabled
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.trigger()
    }
    scale: mouse.pressed ? 0.985 : 1
    Behavior on scale { NumberAnimation { duration: Design.fastMotion } }
}
