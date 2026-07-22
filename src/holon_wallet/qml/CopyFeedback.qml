import QtQuick
import "."

Item {
    id: root
    property bool shown: false
    width: 62; height: 26
    visible: opacity > 0.01
    opacity: shown ? 1 : 0

    function show() {
        shown = true
        hideTimer.restart()
    }

    Rectangle {
        anchors.fill: parent
        radius: 10
        color: Design.accentSoft
        border.width: 1
        border.color: "#4D84C7BA"
    }
    Text {
        anchors.centerIn: parent
        text: "Copied"
        color: Design.accent
        font.family: Design.fontFamily
        font.pixelSize: 11
        font.weight: Font.DemiBold
    }
    Timer {
        id: hideTimer
        interval: 1800
        repeat: false
        onTriggered: root.shown = false
    }
    Behavior on opacity { NumberAnimation { duration: Design.normalMotion } }
}
