import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root

    BackButton {
        objectName: "backupBackButton"; x: 22; y: 42
        onTriggered: walletController.cancelFlow()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 49
        text: "Backup Seed Phrase"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 97
        text: "Store these words securely and in the exact order"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 119
        text: "Anyone with access can control this Account"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
    }
    Rectangle {
        x: 22; y: 158; width: 470; height: 306; radius: 14
        color: "#7A0B1225"; border.width: 1.4; border.color: Design.purple
        GlowWave { x: 185; y: 246; width: 285; height: 60; opacity: 0.38 }
        Repeater {
            model: walletController.backupWords
            delegate: Item {
                required property string modelData
                required property int index
                width: 145; height: 63
                x: 12 + (index % 3) * 150
                y: 18 + Math.floor(index / 3) * 69
                Rectangle {
                    anchors.fill: parent; radius: 9
                    color: "#5A111A31"; border.width: 1; border.color: Design.borderSoft
                }
                Text {
                    x: 13; anchors.verticalCenter: parent.verticalCenter
                    text: (index + 1) + "."; color: Design.textFaint
                    font.family: Design.fontFamily; font.pixelSize: 11
                }
                Text {
                    x: 42; anchors.verticalCenter: parent.verticalCenter
                    text: modelData; color: Design.text
                    font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
                }
            }
        }
    }
    FormButton {
        objectName: "copySeedButton"
        x: 54; y: 482; width: 406; height: 58
        label: "Copy Seed Phrase"; primary: false
        onTriggered: walletController.copyBackup()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 551
        text: walletController.errorMessage; color: "#FF7F9B"
        font.family: Design.fontFamily; font.pixelSize: 11
    }
    FormButton {
        objectName: "finishBackupButton"
        x: 54; y: 576; width: 406; height: 58
        label: "Done · I saved the phrase"
        onTriggered: walletController.finishBackup()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 647
        text: "The Account is saved only after Done"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
    }
}
