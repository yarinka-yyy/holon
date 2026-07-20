import QtQuick
import "."

// qmllint disable unqualified

Item {
    Text {
        x: 24; y: 39; text: "Holon Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Image {
        x: 202; y: 154; width: 110; height: 110
        source: "assets/wallet-outline.svg"; sourceSize: Qt.size(220, 220)
    }
    Rectangle {
        x: 197; y: 149; width: 120; height: 120; radius: 60
        color: "transparent"; border.width: 8; border.color: "#149A63FF"; z: -1
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 289
        text: "Welcome to Holon Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 27; font.weight: Font.DemiBold
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 335
        text: "Create or import your first Account"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 14
    }
    FormButton {
        objectName: "createAccountButton"
        x: 86; y: 421; width: 342; height: 58
        label: "Create a new address"
        onTriggered: walletController.beginCreate()
    }
    FormButton {
        objectName: "importAccountButton"
        x: 86; y: 495; width: 342; height: 58
        label: "Import existing Account"; primary: false
        onTriggered: walletController.beginImport()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 582
        text: "Secrets stay inside this local Wallet"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
    }
}
