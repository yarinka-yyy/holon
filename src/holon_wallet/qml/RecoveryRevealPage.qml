import QtQuick
import Holon.Wallet 1.0
import "."

PageState {
    id: root
    property bool isSeed: walletController.recoveryRevealKind === "seed_phrase"

    ScreenHeader {
        objectName: "recoveryRevealHeader"; x: 28; y: 54; width: 458
        title: root.isSeed ? "Seed Phrase" : "Private Key"
        subtitle: "Visible locally · " + walletController.recoveryRevealSeconds + "s remaining"
        onBackRequested: walletController.finishRecovery()
    }
    Rectangle {
        x: 28; y: 136; width: 458; height: 62; radius: Design.controlRadius
        color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Image {
            x: 16; anchors.verticalCenter: parent.verticalCenter
            width: 24; height: 24; source: "assets/warning.svg"; sourceSize: Qt.size(48, 48)
        }
        Text {
            x: 54; width: 382; anchors.verticalCenter: parent.verticalCenter
            text: "Keep this material private and store it offline."
            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 13
            wrapMode: Text.Wrap
        }
    }
    SurfaceCard {
        x: 28; y: 218; width: 458; height: 348
        RecoverySecretDisplay {
            objectName: "recoverySecretDisplay"
            x: 18; y: 18; width: 422; height: 312
        }
    }
    Text {
        visible: walletController.recoveryRevealDerivationPath.length > 0
        x: 72; y: 578; width: 370; horizontalAlignment: Text.AlignHCenter
        text: "Derived at " + walletController.recoveryRevealDerivationPath
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
    }
    FormButton {
        objectName: "copyRecoveryButton"; x: 72; y: 610; width: 370; height: 56
        label: walletController.recoveryCopyUsed
            ? "Copied · clears in " + walletController.recoveryClipboardSeconds + "s"
            : "Copy Once"
        primary: false; controlEnabled: !walletController.recoveryCopyUsed
        onTriggered: walletController.copyRecoveryMaterial()
    }
    FormButton {
        objectName: "finishRecoveryButton"; x: 72; y: 686; width: 370; height: 56
        label: "Done · Hide Material"; onTriggered: walletController.finishRecovery()
    }
    Text {
        x: 72; y: 758; width: 370; horizontalAlignment: Text.AlignHCenter
        text: "Copy is cleared only if clipboard content is unchanged"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
