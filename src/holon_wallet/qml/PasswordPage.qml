import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root

    function submit() {
        walletController.submitPassword(passwordField.text, confirmField.text)
    }
    onEnabledChanged: if (!enabled) { passwordField.clear(); confirmField.clear() }

    BackButton {
        objectName: "passwordBackButton"
        x: 22; y: 42; visible: walletController.passwordTitle !== "Unlock Wallet"
        onTriggered: walletController.cancelFlow()
    }
    Text {
        x: 24; y: 39; visible: walletController.passwordTitle === "Unlock Wallet"
        text: "Holon Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Image {
        x: 207; y: 135; width: 100; height: 100
        source: "assets/shield-lock.svg"; sourceSize: Qt.size(200, 200)
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 257
        text: walletController.passwordTitle; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 29; font.weight: Font.DemiBold
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 304
        text: walletController.passwordSubtitle; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 13
    }
    PasswordInput {
        id: passwordField; objectName: "passwordField"
        fieldObjectName: "passwordTextInput"
        x: 86; y: 354; width: 342; height: 61
        placeholderText: "Enter password"; onAccepted: root.submit()
    }
    PasswordInput {
        id: confirmField; objectName: "confirmPasswordField"
        fieldObjectName: "confirmPasswordTextInput"
        x: 86; y: 430; width: 342; height: 61
        visible: walletController.passwordConfirmRequired
        placeholderText: "Confirm password"; onAccepted: root.submit()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        y: walletController.passwordConfirmRequired ? 502 : 430
        text: walletController.errorMessage; color: "#FF7F9B"
        font.family: Design.fontFamily; font.pixelSize: 11
    }
    FormButton {
        objectName: "passwordSubmitButton"
        x: 86; width: 342; height: 58
        y: walletController.passwordConfirmRequired ? 529 : 470
        label: walletController.passwordActionLabel
        controlEnabled: passwordField.text.length >= 4
            && (!walletController.passwordConfirmRequired
                || (confirmField.text.length >= 4 && passwordField.text === confirmField.text))
        onTriggered: root.submit()
    }
}
