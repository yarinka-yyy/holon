import QtQuick
import "."

PageState {
    id: root
    property var account: walletController.activeProfile
    property bool seedSelected: walletController.recoverySelection === "seed_phrase"

    ScreenHeader {
        objectName: "recoveryReviewHeader"; x: 28; y: 54; width: 458
        title: "Recovery Material"; subtitle: "Review the active Account"
        onBackRequested: walletController.finishRecovery()
    }
    Rectangle {
        x: 28; y: 136; width: 458; height: 70; radius: Design.controlRadius
        color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Image {
            x: 16; anchors.verticalCenter: parent.verticalCenter
            width: 24; height: 24; source: "assets/warning.svg"; sourceSize: Qt.size(48, 48)
        }
        Text {
            x: 54; width: 382; anchors.verticalCenter: parent.verticalCenter
            text: "Anyone who sees this material can control this Account."
            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 13
            wrapMode: Text.Wrap
        }
    }
    SurfaceCard {
        x: 28; y: 226; width: 458; height: 154
        Text {
            x: 18; y: 17; text: "ACTIVE ACCOUNT"; color: Design.textFaint
            font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
        }
        Text {
            x: 18; y: 48; text: root.account.label || "Account"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold
        }
        Text {
            x: 18; y: 78; width: parent.width - 36; text: root.account.address || ""
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
            wrapMode: Text.WrapAnywhere
        }
        Text {
            x: 18; y: 124; text: root.account.typeLabel || ""; color: Design.accent
            font.family: Design.fontFamily; font.pixelSize: 12; font.weight: Font.Medium
        }
    }
    Text {
        x: 28; y: 408; text: "Choose material"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 16; font.weight: Font.DemiBold
    }
    Rectangle {
        id: seedChoice; objectName: "recoverySeedChoice"
        function trigger() { walletController.selectRecoveryMaterial("seed_phrase") }
        visible: walletController.recoverySeedAvailable
        x: 28; y: 446; width: 219; height: 96; radius: Design.controlRadius
        color: root.seedSelected ? Design.accentSoft : Design.surface
        border.width: root.seedSelected ? 2 : 1
        border.color: root.seedSelected ? Design.accent : Design.border
        Text {
            x: 16; y: 19; text: "Seed Phrase"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.DemiBold
        }
        Text {
            x: 16; y: 51; width: parent.width - 32; text: "12 or 24 recovery words"
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        }
        MouseArea {
            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
            onClicked: seedChoice.trigger()
        }
    }
    Rectangle {
        id: keyChoice; objectName: "recoveryPrivateKeyChoice"
        function trigger() { walletController.selectRecoveryMaterial("private_key") }
        x: walletController.recoverySeedAvailable ? 267 : 28; y: 446
        width: walletController.recoverySeedAvailable ? 219 : 458
        height: 96; radius: Design.controlRadius
        color: !root.seedSelected ? Design.accentSoft : Design.surface
        border.width: !root.seedSelected ? 2 : 1
        border.color: !root.seedSelected ? Design.accent : Design.border
        Text {
            x: 16; y: 19; text: "Private Key"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.DemiBold
        }
        Text {
            x: 16; y: 51; width: parent.width - 32
            text: walletController.recoverySeedAvailable
                ? "Derived Account key" : "Imported Account key"
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        }
        MouseArea {
            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
            onClicked: keyChoice.trigger()
        }
    }
    Text {
        x: 72; y: 566; width: 370; horizontalAlignment: Text.AlignHCenter
        text: walletController.errorMessage; color: Design.danger
        font.family: Design.fontFamily; font.pixelSize: 12; wrapMode: Text.Wrap
    }
    FormButton {
        objectName: "recoveryReviewContinue"; x: 72; y: 620; width: 370; height: 56
        label: "Continue to Confirm"; onTriggered: walletController.prepareRecovery()
    }
    Text {
        x: 72; y: 694; width: 370; horizontalAlignment: Text.AlignHCenter
        text: "A new exact action expires after five minutes"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
