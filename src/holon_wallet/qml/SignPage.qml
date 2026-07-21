import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    property bool readyToSign: passwordField.text.length >= 4
        && !walletController.offlineSigningInProgress

    function submit() {
        if (walletController.offlineSigningInProgress)
            return
        let oneTimePassword = passwordField.text
        passwordField.clear()
        walletController.submitOfflineSigning(oneTimePassword)
        oneTimePassword = ""
    }
    onEnabledChanged: if (!enabled) passwordField.clear()

    Text {
        x: 24; y: 39; text: "Holon Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Image {
        x: 202; y: 108; width: 110; height: 110
        source: "assets/sign-document.svg"; sourceSize: Qt.size(220, 220)
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 239
        text: "Sign this transaction locally"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.DemiBold
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 282
        text: "Fresh password authorizes this exact action once"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
    }
    Rectangle {
        x: 105; y: 312; width: 304; height: 28; radius: 8
        color: "#182849"; border.width: 1; border.color: "#38558E"
        Text {
            anchors.centerIn: parent; text: "OFFLINE ONLY  ·  NOTHING WILL BE SENT"
            color: "#87A6FF"; font.family: Design.fontFamily
            font.pixelSize: 9; font.weight: Font.Bold; font.letterSpacing: 0.3
        }
    }
    PasswordInput {
        id: passwordField; objectName: "offlineSigningPasswordField"
        fieldObjectName: "offlineSigningPasswordInput"
        x: 86; y: 365; width: 342; height: 61
        placeholderText: "Enter your wallet password"
        onAccepted: root.submit()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 439
        text: walletController.offlineSigningInProgress
            ? "Authenticating and verifying locally…"
            : "The password is not stored and cannot be reused"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
    }
    FormButton {
        objectName: "offlineSignButton"
        x: 86; y: 478; width: 342; height: 58
        label: walletController.offlineSigningInProgress ? "Signing locally…" : "Sign transaction"
        primary: root.readyToSign
        controlEnabled: root.readyToSign
        onTriggered: root.submit()
    }
    FormButton {
        objectName: "offlineSignCancelButton"
        x: 86; y: 552; width: 342; height: 48
        label: "Cancel"; primary: false; controlEnabled: true
        onTriggered: walletController.cancelOfflineSigning()
    }
}
