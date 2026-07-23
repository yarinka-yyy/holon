import QtQuick
import "."

PageState {
    id: root
    function submit() { walletController.submitPassword(passwordField.text, confirmField.text) }
    onEnabledChanged: if (!enabled) { passwordField.clear(); confirmField.clear() }

    ScreenHeader {
        objectName: "password"; x: 28; y: 54; width: 458
        title: walletController.passwordTitle
        subtitle: walletController.passwordSubtitle
        backVisible: true
        onBackRequested: walletController.cancelFlow()
    }
    SurfaceCard {
        x: 86; y: 178; width: 342; height: 174
        Rectangle {
            anchors.centerIn: parent; width: 84; height: 84; radius: 42
            color: Design.accentSoft; border.width: 1; border.color: Design.accent
            Image {
                anchors.centerIn: parent; width: 42; height: 42
                source: "assets/lock.svg"; sourceSize: Qt.size(84, 84)
            }
        }
    }
    Text {
        x: 72; y: 389; width: 370; horizontalAlignment: Text.AlignHCenter
        text: "Use one Wallet password for protected actions"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 14
        wrapMode: Text.Wrap
    }
    PasswordInput {
        id: passwordField; objectName: "passwordField"
        fieldObjectName: "passwordTextInput"
        x: 72; y: 454; width: 370; height: 56
        placeholderText: "Enter password"; onAccepted: root.submit()
    }
    PasswordInput {
        id: confirmField; objectName: "confirmPasswordField"
        fieldObjectName: "confirmPasswordTextInput"
        x: 72; y: 526; width: 370; height: 56
        visible: walletController.passwordConfirmRequired
        placeholderText: "Confirm password"; onAccepted: root.submit()
    }
    Text {
        x: 72; y: walletController.passwordConfirmRequired ? 598 : 526
        width: 370; horizontalAlignment: Text.AlignHCenter
        text: walletController.errorMessage; color: Design.danger
        font.family: Design.fontFamily; font.pixelSize: 12
    }
    FormButton {
        objectName: "passwordSubmitButton"; x: 72; width: 370; height: 56
        y: walletController.passwordConfirmRequired ? 632 : 568
        label: walletController.passwordActionLabel
        controlEnabled: passwordField.text.length >= 4
            && (!walletController.passwordConfirmRequired
                || (confirmField.text.length >= 4 && passwordField.text === confirmField.text))
        onTriggered: root.submit()
    }
    Text {
        visible: walletController.passwordConfirmRequired
        x: 72; y: 708; width: 370; horizontalAlignment: Text.AlignHCenter
        text: "Minimum 4 characters · a longer password is safer"
        color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
