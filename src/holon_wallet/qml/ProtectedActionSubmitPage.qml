import QtQuick
import "."

TransactionFlowShell {
    title: "Submitting Protected Action"
    subtitle: "Do not close Holon Wallet"
    activeStep: 2; backVisible: false
    SurfaceCard {
        x: 0; y: 38; width: 458; height: 280
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter; y: 40
            width: 92; height: 92; radius: 46
            color: Design.accentSoft; border.width: 1; border.color: Design.accent
            Rectangle {
                anchors.centerIn: parent; width: 54; height: 54; radius: 27
                color: "transparent"; border.width: 5; border.color: Design.accent
                Rectangle { x: 22; y: -6; width: 10; height: 12; color: Design.accentSoft }
                RotationAnimation on rotation {
                    running: true; from: 0; to: 360; duration: 900; loops: Animation.Infinite
                }
            }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; y: 158
            text: "Sequential submit-once execution"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 20; font.weight: Font.DemiBold
        }
        Text {
            x: 30; y: 202; width: 398; horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: "Each phase is rechecked, signed locally, submitted once, and reconciled before the next phase."
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
    Rectangle {
        x: 0; y: 346; width: 458; height: 82; radius: Design.controlRadius
        color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Text {
            anchors.centerIn: parent; width: 410; horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: "Execution cannot be cancelled after submission starts. Unknown outcomes are never retried."
            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
}
