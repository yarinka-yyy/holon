import QtQuick
import "."

Item {
    id: root
    property bool suggested: false
    width: 40; height: 40
    visible: opacity > 0
    opacity: suggested ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Design.normalMotion } }

    Rectangle {
        anchors.centerIn: parent; width: 28; height: 28; radius: 14
        color: "#18332F"; border.width: 1; border.color: "#4D84C7BA"
    }
    Canvas {
        id: arrow
        x: 11; y: 10; width: 18; height: 18
        onPaint: {
            const context = getContext("2d")
            context.clearRect(0, 0, width, height)
            context.strokeStyle = Design.accent
            context.lineWidth = 2.2
            context.lineCap = "round"
            context.lineJoin = "round"
            context.beginPath()
            context.moveTo(3, 6)
            context.lineTo(9, 12)
            context.lineTo(15, 6)
            context.stroke()
        }
        SequentialAnimation on y {
            running: root.suggested; loops: Animation.Infinite
            NumberAnimation { from: 9; to: 13; duration: 760; easing.type: Easing.InOutSine }
            NumberAnimation { from: 13; to: 9; duration: 760; easing.type: Easing.InOutSine }
        }
    }
}
