import QtQuick
import "."

// qmllint disable unqualified

Item {
    Text {
        x: 24; y: 39; text: "Holon Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Image {
        x: 207; y: 160; width: 100; height: 100
        source: "assets/warning.svg"; sourceSize: Qt.size(200, 200)
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 291
        text: "Wallet unavailable"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 28; font.weight: Font.DemiBold
    }
    Text {
        x: 72; y: 341; width: 370; horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        text: "The existing Wallet data is unreadable or uses an unsupported version. It was not changed."
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
    }
    FormButton {
        objectName: "retryWalletButton"
        x: 86; y: 438; width: 342; height: 58; label: "Retry"
        onTriggered: walletController.retryUnavailable()
    }
    FormButton {
        objectName: "exitWalletButton"
        x: 86; y: 512; width: 342; height: 58; label: "Close Wallet"; primary: false
        onTriggered: walletWindow.close()
    }
}
