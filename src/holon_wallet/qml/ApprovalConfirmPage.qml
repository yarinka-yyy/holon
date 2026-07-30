import QtQuick
import "."

TransactionFlowShell {
    id: root
    title: "Confirm Revoke"; subtitle: "Authorize this exact action once"
    activeStep: 1; onBackRequested: walletController.cancelRevoke()
    property var action: walletController.revokeAction
    property bool ready: passwordField.text.length >= 4
        && walletController.revokeExecutionAvailable

    function submit() {
        if (!ready) return
        let oneTimePassword = passwordField.text
        passwordField.clear()
        walletController.submitRevoke(oneTimePassword)
        oneTimePassword = ""
    }
    onEnabledChanged: if (!enabled) passwordField.clear()

    SurfaceCard {
        x: 0; y: 0; width: 458; height: 168
        Image {
            anchors.right: parent.right; anchors.rightMargin: 18; y: 20
            width: 50; height: 50; source: "assets/usdc.webp"
            sourceSize: Qt.size(100, 100)
        }
        Text {
            x: 18; y: 18; width: 350
            text: "Revoke USDC on " + (root.action.network || "")
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 19; font.weight: Font.DemiBold
        }
        Text {
            x: 18; y: 54; width: 350
            text: "Spender " + (root.action.spender || "")
            color: Design.textMuted; font.family: Design.fontFamily
            font.pixelSize: 11; elide: Text.ElideMiddle
        }
        Text {
            x: 18; y: 104; width: 422
            text: (root.action.allowanceBefore || "Allowance") + " → 0 USDC"
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 13
        }
        Text {
            x: 18; y: 139
            text: "Maximum fee " + walletController.revokeFeeUsd
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    Text {
        x: 0; y: 190; text: "Wallet password"; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 13
    }
    PasswordInput {
        id: passwordField; objectName: "revokePasswordField"
        fieldObjectName: "revokePasswordInput"
        x: 0; y: 218; width: 458; height: 56
        placeholderText: "Enter fresh password"
    }
    Text {
        x: 0; y: 287; width: 458; horizontalAlignment: Text.AlignHCenter
        text: "The password is used once and is not stored"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
    Text {
        x: 18; y: 330; width: 422; horizontalAlignment: Text.AlignHCenter
        text: "The button below authorizes only the exact revoke shown above."
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        wrapMode: Text.Wrap
    }
    FormButton {
        objectName: "revokeSubmitButton"; x: 0; y: 474; width: 458; height: 56
        label: "Sign and revoke"; controlEnabled: root.ready
        onTriggered: root.submit()
    }
    FormButton {
        objectName: "revokeCancelButton"; x: 0; y: 542; width: 458; height: 42
        label: "Cancel"; primary: false; onTriggered: walletController.cancelRevoke()
    }
}
