import QtQuick
import "."

TransactionFlowShell {
    id: root
    title: "Confirm Transaction"; subtitle: "Authorize this exact transfer once"
    activeStep: 1; onBackRequested: walletController.cancelMainnetExecution()
    property bool explicitlyConfirmed: false
    property var action: walletController.transferAction
    property bool readyToSign: passwordField.text.length >= 4
        && explicitlyConfirmed && walletController.mainnetExecutionAvailable

    function submit() {
        if (!readyToSign) return
        let oneTimePassword = passwordField.text
        passwordField.clear(); explicitlyConfirmed = false
        walletController.submitMainnetExecution(oneTimePassword, true)
        oneTimePassword = ""
    }
    onEnabledChanged: if (!enabled) { passwordField.clear(); explicitlyConfirmed = false }

    SurfaceCard {
        x: 0; y: 0; width: 458; height: 118
        Text {
            x: 18; y: 16; text: "1 USDC on Base"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 20; font.weight: Font.DemiBold
        }
        Text {
            x: 18; y: 50; text: "To " + (root.action.shortRecipient || "")
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
        }
        Text {
            x: 18; y: 78; text: "Maximum fee " + (root.action.maxFeeDisplay || "Unavailable")
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        }
        Image {
            anchors.right: parent.right; anchors.rightMargin: 18; y: 20
            width: 50; height: 50; source: "assets/usdc.svg"; sourceSize: Qt.size(100, 100)
        }
    }
    Text {
        x: 0; y: 148; text: "Wallet password"; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 13
    }
    PasswordInput {
        id: passwordField; objectName: "mainnetPasswordField"
        fieldObjectName: "mainnetPasswordInput"
        x: 0; y: 176; width: 458; height: 56
        placeholderText: "Enter fresh password"; onAccepted: root.submit()
    }
    Text {
        x: 0; y: 245; width: 458; horizontalAlignment: Text.AlignHCenter
        text: "The password is used once and is not stored"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
    Item {
        objectName: "mainnetConfirmationCheckbox"
        x: 0; y: 282; width: 458; height: 72
        function trigger() { root.explicitlyConfirmed = !root.explicitlyConfirmed }
        SurfaceCard { anchors.fill: parent; interactive: true; onTriggered: parent.trigger() }
        Rectangle {
            x: 16; anchors.verticalCenter: parent.verticalCenter
            width: 28; height: 28; radius: 8
            color: root.explicitlyConfirmed ? Design.accent : Design.surfaceSecondary
            border.width: 1; border.color: root.explicitlyConfirmed ? Design.accent : Design.borderStrong
            Image {
                anchors.centerIn: parent; width: 20; height: 20
                visible: root.explicitlyConfirmed; source: "assets/check.svg"; sourceSize: Qt.size(40, 40)
            }
        }
        Text {
            x: 60; width: 378; anchors.verticalCenter: parent.verticalCenter
            text: "I confirm this irreversible transfer of 1 real USDC on Base Mainnet."
            wrapMode: Text.Wrap; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 13
        }
    }
    Rectangle {
        x: 0; y: 376; width: 458; height: 64; radius: Design.controlRadius
        color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Text {
            anchors.centerIn: parent; width: 414; horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap; text: "Real funds · signing and submission cannot be undone once started"
            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
    FormButton {
        objectName: "mainnetSendButton"; x: 0; y: 468; width: 458; height: 56
        label: "Sign and send 1 USDC"; controlEnabled: root.readyToSign
        onTriggered: root.submit()
    }
    FormButton {
        objectName: "mainnetCancelButton"; x: 0; y: 538; width: 458; height: 48
        label: "Cancel"; primary: false
        onTriggered: walletController.cancelMainnetExecution()
    }
}
