import QtQuick
import "."

PageState {
    id: root
    property bool applyMode: walletController.trustedApplyMode
    property string operation: walletController.trustedPolicyOperation
    function submit() {
        if (root.applyMode)
            walletController.submitTrustedApply(passwordField.text)
        else
            walletController.submitTrustedDraft(passwordField.text)
    }
    onEnabledChanged: if (!enabled) passwordField.clear()

    ScreenHeader {
        objectName: "trustedPasswordHeader"; x: 28; y: 54; width: 458
        title: root.operation === "initialize" ? "Confirm Initialization"
            : root.operation === "activate" ? "Confirm Activation"
            : root.operation === "deactivate" ? "Confirm Deactivation"
            : root.applyMode ? "Confirm Apply" : "Confirm Draft"
        subtitle: "Fresh local authentication"
        onBackRequested: root.applyMode
            ? walletController.closeTrustedApplyPassword()
            : walletController.closeTrustedDraftPassword()
    }
    SurfaceCard {
        x: 86; y: 170; width: 342; height: 174
        Rectangle {
            anchors.centerIn: parent; width: 84; height: 84; radius: 42
            color: Design.accentSoft; border.width: 1; border.color: Design.accent
            Image { anchors.centerIn: parent; width: 42; height: 42; source: "assets/lock.svg"; sourceSize: Qt.size(84, 84) }
        }
    }
    Text {
        x: 72; y: 382; width: 370; horizontalAlignment: Text.AlignHCenter
        text: root.operation === "initialize"
            ? "Authenticate one-time authority-state initialization. Send and Lending remain disabled."
            : root.operation === "activate"
            ? "Authenticate enabling the reviewed Lending routes. Send remains disabled."
            : root.operation === "deactivate"
            ? "Authenticate disabling Lending authority."
            : root.applyMode
            ? "Authenticate applying this exact saved draft. All authority remains disabled."
            : "Authenticate this complete draft. This does not activate transfer authority."
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 14; wrapMode: Text.Wrap
    }
    PasswordInput {
        id: passwordField; objectName: "trustedPasswordField"
        fieldObjectName: "trustedPasswordInput"
        x: 72; y: 470; width: 370; height: 56
        placeholderText: "Enter Wallet password"; onAccepted: root.submit()
    }
    Text {
        objectName: "trustedPasswordError"; x: 72; y: 546; width: 370
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        text: walletController.errorMessage; color: Design.danger
        font.family: Design.fontFamily; font.pixelSize: 12
    }
    FormButton {
        objectName: "trustedPasswordSubmitButton"; x: 72; y: 600; width: 370; height: 56
        label: root.operation === "initialize" ? "Initialize Authority"
            : root.operation === "activate" ? "Activate Lending"
            : root.operation === "deactivate" ? "Deactivate Lending"
            : root.applyMode ? "Apply Disabled Draft" : "Save Disabled Draft"
        controlEnabled: passwordField.text.length >= 4
        onTriggered: root.submit()
    }
    Text {
        x: 72; y: 682; width: 370; horizontalAlignment: Text.AlignHCenter
        text: root.applyMode
            ? "Password stays inside Wallet and is never sent to Guard"
            : "Password stays inside Wallet and authorizes this save only"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
