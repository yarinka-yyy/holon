import QtQuick
import "."

Item {
    id: root
    property var steps: []
    property int activeStep: 0
    height: 72
    Rectangle {
        x: 24; y: 17; width: parent.width - 48; height: 1; color: Design.borderStrong
    }
    Repeater {
        model: root.steps
        delegate: Item {
            required property string modelData
            required property int index
            x: index * (root.width - 48) / Math.max(1, root.steps.length - 1)
            width: 48; height: 72
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter; y: 6
                width: 23; height: 23; radius: 12
                color: index < root.activeStep ? Design.accent
                    : index === root.activeStep ? Design.accentSoft : Design.background
                border.width: index === root.activeStep ? 6 : 1
                border.color: index <= root.activeStep ? Design.accent : Design.textMuted
                Behavior on color { ColorAnimation { duration: Design.normalMotion } }
                Behavior on border.width { NumberAnimation { duration: Design.normalMotion } }
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter; y: 39
                text: modelData; color: index === root.activeStep ? Design.accent : Design.textMuted
                font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
            }
        }
    }
}
