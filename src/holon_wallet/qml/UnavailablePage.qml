import QtQuick
import "."

PageState {
    Text {
        x: 28; y: 54; text: "Holon Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 24; font.weight: Font.DemiBold
    }
    SurfaceCard {
        x: 86; y: 190; width: 342; height: 280
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter; y: 42
            width: 88; height: 88; radius: 44
            color: "#332C2020"; border.width: 1; border.color: Design.danger
            Image {
                anchors.centerIn: parent; width: 46; height: 46
                source: "assets/warning.svg"; sourceSize: Qt.size(92, 92)
            }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; y: 158
            text: "Wallet unavailable"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 22; font.weight: Font.DemiBold
        }
        Text {
            x: 28; y: 202; width: parent.width - 56
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: "The existing vault cannot be safely opened. No data was replaced."
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
        }
    }
    FormButton {
        objectName: "retryUnavailableButton"; x: 86; y: 506; width: 342; height: 56
        label: "Try again"; primary: false; onTriggered: walletController.retryUnavailable()
    }
}
