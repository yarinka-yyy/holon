import QtQuick
import "."

TransactionFlowShell {
    title: "Revoking Approval"; subtitle: "Do not close Holon Wallet"
    activeStep: 2; backVisible: false
    SurfaceCard {
        x: 0; y: 28; width: 458; height: 286
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter; y: 42
            width: 96; height: 96; radius: 48
            color: Design.accentSoft; border.width: 1; border.color: Design.accent
            Rectangle {
                anchors.centerIn: parent; width: 58; height: 58; radius: 29
                color: "transparent"; border.width: 5; border.color: Design.accent
                Rectangle { x: 24; y: -6; width: 10; height: 12; color: Design.accentSoft }
                RotationAnimation on rotation {
                    running: true; from: 0; to: 360; duration: 900
                    loops: Animation.Infinite
                }
            }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; y: 164
            text: "Submitting revoke once"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 22; font.weight: Font.DemiBold
        }
        Text {
            x: 30; y: 206; width: parent.width - 60
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: "Revalidating allowance, signing locally, and making at most one broadcast request."
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
        }
    }
    Rectangle {
        x: 0; y: 342; width: 458; height: 80; radius: Design.controlRadius
        color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Text {
            anchors.centerIn: parent; width: 410
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: "Submission has started and cannot be cancelled. No automatic retry will occur."
            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
}
