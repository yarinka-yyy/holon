import QtQuick
import QtQuick.Shapes
import "."

Item {
    id: wave
    opacity: 0.92

    Shape {
        anchors.fill: parent
        layer.enabled: true
        layer.samples: 4

        ShapePath {
            fillColor: "transparent"
            strokeColor: "#14265BFF"
            strokeWidth: 22
            capStyle: ShapePath.RoundCap
            startX: 0
            startY: wave.height * 0.83
            PathCubic {
                control1X: wave.width * 0.34
                control1Y: wave.height * 0.70
                control2X: wave.width * 0.62
                control2Y: wave.height * 0.26
                x: wave.width
                y: wave.height * 0.12
            }
        }

        ShapePath {
            fillColor: "transparent"
            strokeColor: "#445E3BFF"
            strokeWidth: 9
            capStyle: ShapePath.RoundCap
            startX: 0
            startY: wave.height * 0.83
            PathCubic {
                control1X: wave.width * 0.34
                control1Y: wave.height * 0.70
                control2X: wave.width * 0.62
                control2Y: wave.height * 0.26
                x: wave.width
                y: wave.height * 0.12
            }
        }

        ShapePath {
            fillColor: "transparent"
            strokeColor: Design.purple
            strokeWidth: 2.2
            capStyle: ShapePath.RoundCap
            startX: 0
            startY: wave.height * 0.83
            PathCubic {
                control1X: wave.width * 0.34
                control1Y: wave.height * 0.70
                control2X: wave.width * 0.62
                control2Y: wave.height * 0.26
                x: wave.width
                y: wave.height * 0.12
            }
        }

        ShapePath {
            fillColor: "transparent"
            strokeColor: "#723F8CFF"
            strokeWidth: 0.9
            startX: wave.width * 0.08
            startY: wave.height
            PathCubic {
                control1X: wave.width * 0.44
                control1Y: wave.height * 0.62
                control2X: wave.width * 0.69
                control2Y: wave.height * 0.42
                x: wave.width
                y: 0
            }
        }
    }
}
