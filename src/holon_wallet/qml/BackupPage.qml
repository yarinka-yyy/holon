import QtQuick
import "."

PageState {
    ScreenHeader {
        objectName: "backup"; x: 28; y: 54; width: 458
        title: "Backup Seed Phrase"; subtitle: "Write the words in their exact order"
        onBackRequested: walletController.cancelFlow()
    }
    Rectangle {
        x: 28; y: 142; width: 458; height: 62; radius: Design.controlRadius
        color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Image {
            x: 16; anchors.verticalCenter: parent.verticalCenter
            width: 24; height: 24; source: "assets/warning.svg"; sourceSize: Qt.size(48, 48)
        }
        Text {
            x: 54; width: 382; anchors.verticalCenter: parent.verticalCenter
            text: "Anyone with these words can control this Account."
            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 13
            wrapMode: Text.Wrap
        }
    }
    SurfaceCard {
        x: 28; y: 228; width: 458; height: 328
        Repeater {
            model: walletController.backupWords
            delegate: Rectangle {
                required property string modelData
                required property int index
                width: 134; height: 56; radius: 12
                x: 14 + (index % 3) * 146
                y: 16 + Math.floor(index / 3) * 74
                color: Design.surfaceSecondary; border.width: 1; border.color: Design.border
                Text {
                    x: 12; anchors.verticalCenter: parent.verticalCenter
                    text: (index + 1) + "."; color: Design.textFaint
                    font.family: Design.fontFamily; font.pixelSize: 11
                }
                Text {
                    x: 38; anchors.verticalCenter: parent.verticalCenter
                    text: modelData; color: Design.text
                    font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
                }
            }
        }
    }
    FormButton {
        objectName: "copySeedButton"; x: 72; y: 580; width: 370; height: 56
        label: "Copy Seed Phrase"; primary: false
        onTriggered: walletController.copyBackup()
    }
    Text {
        x: 72; y: 648; width: 370; horizontalAlignment: Text.AlignHCenter
        text: walletController.errorMessage; color: Design.danger
        font.family: Design.fontFamily; font.pixelSize: 12
    }
    FormButton {
        objectName: "finishBackupButton"; x: 72; y: 682; width: 370; height: 56
        label: "Done · I saved the phrase"; onTriggered: walletController.finishBackup()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 758
        text: "The Account is saved only after Done"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
