import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    property bool explicitlyConfirmed: false
    property bool readyToSign: passwordField.text.length >= 4
        && root.explicitlyConfirmed
        && !walletController.mainnetExecutionInProgress

    function submit() {
        if (walletController.mainnetExecutionInProgress || !root.explicitlyConfirmed)
            return
        let oneTimePassword = passwordField.text
        let oneTimeConfirmation = root.explicitlyConfirmed
        passwordField.clear()
        root.explicitlyConfirmed = false
        walletController.submitMainnetExecution(oneTimePassword, oneTimeConfirmation)
        oneTimePassword = ""
        oneTimeConfirmation = false
    }
    onEnabledChanged: if (!enabled) {
        passwordField.clear()
        root.explicitlyConfirmed = false
    }

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
        text: "Authorize mainnet transfer"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.DemiBold
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 282
        text: "Fresh password authorizes one exact submission"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
    }
    Rectangle {
        x: 105; y: 312; width: 304; height: 28; radius: 8
        color: "#182849"; border.width: 1; border.color: "#38558E"
        Text {
            anchors.centerIn: parent; text: "BASE MAINNET  ·  REAL FUNDS  ·  IRREVERSIBLE"
            color: "#FFB36D"; font.family: Design.fontFamily
            font.pixelSize: 9; font.weight: Font.Bold; font.letterSpacing: 0.3
        }
    }
    PasswordInput {
        id: passwordField; objectName: "mainnetPasswordField"
        fieldObjectName: "mainnetPasswordInput"
        x: 86; y: 365; width: 342; height: 61
        placeholderText: "Enter your wallet password"
        onAccepted: root.submit()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 439
        text: walletController.mainnetExecutionInProgress
            ? "Revalidating, signing, and submitting once…"
            : "The password is not stored and cannot be reused"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
    }
    Item {
        id: confirmationControl
        objectName: "mainnetConfirmationCheckbox"
        x: 86; y: 458; width: 342; height: 42
        enabled: !walletController.mainnetExecutionInProgress
        function trigger() {
            if (enabled)
                root.explicitlyConfirmed = !root.explicitlyConfirmed
        }
        Rectangle {
            x: 0; anchors.verticalCenter: parent.verticalCenter
            width: 24; height: 24; radius: 6
            color: root.explicitlyConfirmed ? Design.purple : Design.surface
            border.width: 1
            border.color: root.explicitlyConfirmed ? Design.purpleBright : Design.border
            Text {
                anchors.centerIn: parent; text: root.explicitlyConfirmed ? "✓" : ""
                color: "white"; font.family: Design.fontFamily
                font.pixelSize: 16; font.weight: Font.Bold
            }
        }
        Text {
            x: 36; width: 306; anchors.verticalCenter: parent.verticalCenter
            text: "I understand this sends 1 real USDC on Base Mainnet"
            wrapMode: Text.WordWrap; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 10
        }
        MouseArea {
            anchors.fill: parent; enabled: parent.enabled
            cursorShape: Qt.PointingHandCursor; onClicked: parent.trigger()
        }
    }
    FormButton {
        objectName: "mainnetSendButton"
        x: 86; y: 514; width: 342; height: 58
        label: walletController.mainnetExecutionInProgress
            ? "Submitting once…" : "Sign and send 1 USDC"
        primary: root.readyToSign
        controlEnabled: root.readyToSign
        onTriggered: root.submit()
    }
    FormButton {
        objectName: "mainnetCancelButton"
        x: 86; y: 584; width: 342; height: 44
        label: "Cancel"; primary: false
        controlEnabled: !walletController.mainnetExecutionInProgress
        onTriggered: walletController.cancelMainnetExecution()
    }
}
