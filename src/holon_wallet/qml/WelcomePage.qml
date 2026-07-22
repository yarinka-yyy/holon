import QtQuick
import "."

PageState {
    Text {
        x: 28; y: 54; text: "Holon Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 24; font.weight: Font.DemiBold
    }
    SurfaceCard {
        x: 151; y: 174; width: 212; height: 212
        Rectangle {
            anchors.centerIn: parent; width: 108; height: 108; radius: 54
            color: Design.accentSoft; border.width: 1; border.color: Design.accent
            Image {
                anchors.centerIn: parent; width: 58; height: 58
                source: "assets/wallet-outline.svg"; sourceSize: Qt.size(116, 116)
            }
        }
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 424
        text: "Your local Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 27; font.weight: Font.DemiBold
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 468
        text: "Create or import an Account to get started"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 14
    }
    FormButton {
        objectName: "createAccountButton"; x: 72; y: 548; width: 370; height: 56
        label: "Create a new address"; onTriggered: walletController.beginCreate()
    }
    FormButton {
        objectName: "importAccountButton"; x: 72; y: 620; width: 370; height: 56
        label: "Import existing Account"; primary: false
        onTriggered: walletController.beginImport()
    }
    Row {
        anchors.horizontalCenter: parent.horizontalCenter; y: 720; spacing: 9
        Image { width: 18; height: 18; source: "assets/lock.svg"; sourceSize: Qt.size(36, 36) }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: "Secrets stay encrypted on this device"
            color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
}
