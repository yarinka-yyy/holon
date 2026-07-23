import QtQuick
import "."

TransactionFlowShell {
    id: root
    title: "Confirm Revoke"; subtitle: "Authorize this exact action once"
    activeStep: 1; onBackRequested: walletController.cancelRevoke()
    property bool explicitlyConfirmed: false
    property var action: walletController.revokeAction
    property bool ready: passwordField.text.length >= 4
        && explicitlyConfirmed && walletController.revokeExecutionAvailable

    function submit() {
        if (!ready) return
        let oneTimePassword = passwordField.text
        passwordField.clear(); explicitlyConfirmed = false
        walletController.submitRevoke(oneTimePassword, true)
        oneTimePassword = ""
    }
    onEnabledChanged: if (!enabled) { passwordField.clear(); explicitlyConfirmed = false }

    SurfaceCard {
        x: 0; y: 0; width: 458; height: 154
        Image {
            anchors.right: parent.right; anchors.rightMargin: 18; y: 20
            width: 50; height: 50; source: "assets/usdc.png"
            sourceSize: Qt.size(100, 100)
        }
        Text {
            x: 18; y: 16; width: 350
            text: "Revoke USDC on " + (root.action.network || "")
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 19; font.weight: Font.DemiBold
        }
        Text {
            x: 18; y: 50; width: 350
            text: "Spender " + (root.action.spender || "")
            color: Design.textMuted; font.family: Design.fontFamily
            font.pixelSize: 11; wrapMode: Text.WrapAnywhere
        }
        Text {
            x: 18; y: 104; width: 422
            text: (root.action.allowanceBefore || "Allowance") + " → 0 USDC"
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 13
        }
        Text {
            x: 18; y: 129
            text: "Maximum fee " + (root.action.maxFeeDisplay || "Unavailable")
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    Text {
        x: 0; y: 168; text: "Wallet password"; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 13
    }
    PasswordInput {
        id: passwordField; objectName: "revokePasswordField"
        fieldObjectName: "revokePasswordInput"
        x: 0; y: 196; width: 458; height: 56
        placeholderText: "Enter fresh password"; onAccepted: root.submit()
    }
    Text {
        x: 0; y: 265; width: 458; horizontalAlignment: Text.AlignHCenter
        text: "The password is used once and is not stored"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
    Item {
        objectName: "revokeConfirmationCheckbox"
        x: 0; y: 302; width: 458; height: 82
        function trigger() { root.explicitlyConfirmed = !root.explicitlyConfirmed }
        SurfaceCard { anchors.fill: parent; interactive: true; onTriggered: parent.trigger() }
        Rectangle {
            x: 16; anchors.verticalCenter: parent.verticalCenter
            width: 28; height: 28; radius: 8
            color: root.explicitlyConfirmed ? Design.accent : Design.surfaceSecondary
            border.width: 1
            border.color: root.explicitlyConfirmed ? Design.accent : Design.borderStrong
            Image {
                anchors.centerIn: parent; width: 20; height: 20
                visible: root.explicitlyConfirmed; source: "assets/check.svg"
                sourceSize: Qt.size(40, 40)
            }
        }
        Text {
            x: 60; width: 378; anchors.verticalCenter: parent.verticalCenter
            text: "I confirm that this transaction will completely revoke the exact displayed USDC allowance."
            wrapMode: Text.Wrap; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 13
        }
    }
    Rectangle {
        x: 0; y: 402; width: 458; height: 64; radius: Design.controlRadius
        color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Text {
            anchors.centerIn: parent; width: 414; horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: "A network fee will be paid · submission cannot be undone once started"
            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
    FormButton {
        objectName: "revokeSubmitButton"; x: 0; y: 488; width: 458; height: 56
        label: "Sign and revoke"; controlEnabled: root.ready
        onTriggered: root.submit()
    }
    FormButton {
        objectName: "revokeCancelButton"; x: 0; y: 558; width: 458; height: 48
        label: "Cancel"; primary: false; onTriggered: walletController.cancelRevoke()
    }
}
