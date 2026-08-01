import QtQuick
import "."

PageState {
    id: root
    property var recovery: walletController.lendingRecovery
    readonly property bool supplyReceipt: Boolean(root.recovery.isSupplyReceipt)

    Text {
        x: 28; y: 54; text: root.supplyReceipt ? "Lending supply status" : "Lending supply recovery"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 24; font.weight: Font.DemiBold
    }
    Text {
        x: 28; y: 90; width: 458; wrapMode: Text.Wrap
        text: root.supplyReceipt
            ? "A submitted Supply is being reconciled. Nothing signs, retries, or broadcasts automatically."
            : "A previous approve phase may have left a visible USDC allowance. Nothing resumes or signs automatically."
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
    }
    SurfaceCard {
        x: 28; y: 150; width: 458; height: 250
        Text {
            x: 20; y: 20; text: "Supply to " + (walletController.lendingRecovery.protocolId || "Lending"); color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 19; font.weight: Font.DemiBold
        }
        Text {
            x: 20; y: 58; text: (root.recovery.amount || "—") + " USDC"
            color: Design.accent; font.family: Design.fontFamily; font.pixelSize: 28
            font.weight: Font.DemiBold
        }
        Text {
            x: 20; y: 108; width: 418; wrapMode: Text.Wrap
            text: root.recovery.status || "Recovery state unavailable"
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
        }
        Text {
            x: 20; y: 174; width: 418; elide: Text.ElideMiddle
            text: root.recovery.transactionHash || "No confirmed transaction hash"
            color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
        }
        Image {
            x: 20; y: 210; width: 18; height: 18
            visible: walletController.lendingRecoveryChecking
            source: "assets/refresh.svg"
            RotationAnimation on rotation {
                running: walletController.lendingRecoveryChecking
                from: 0; to: 360; duration: 850; loops: Animation.Infinite
            }
        }
        Text {
            x: 46; y: 211; visible: walletController.lendingRecoveryChecking
            text: root.supplyReceipt ? "Checking Supply receipt…" : "Checking approval receipt…"; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    FormButton {
        objectName: "resumeLendingButton"; x: 28; y: 452; width: 458; height: 56
        label: walletController.lendingRecoveryChecking ? "Checking status…" : (root.supplyReceipt ? "Check Supply status" : "Resume supply")
        controlEnabled: !walletController.lendingRecoveryChecking
        onTriggered: walletController.resumeLendingOperation()
    }
    FormButton {
        objectName: "revokeLendingButton"; x: 28; y: 524; width: 458; height: 56
        label: "Revoke allowance"; primary: false
        controlEnabled: !walletController.lendingRecoveryChecking
        onTriggered: walletController.revokeLendingOperation()
    }
    FormButton {
        objectName: "cancelLendingButton"; x: 28; y: 596; width: 458; height: 56
        label: "Cancel"; primary: false
        controlEnabled: !walletController.lendingRecoveryChecking
        onTriggered: walletController.cancelLendingOperation()
    }
}
