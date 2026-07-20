import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root

    function submit() { walletController.submitMockPassword(passwordField.text) }
    onEnabledChanged: if (!enabled) passwordField.clear()

    Text {
        x: 24; y: 39; text: "Holon Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Image {
        x: 207; y: 118; width: 100; height: 100
        source: "assets/shield-lock.svg"; sourceSize: Qt.size(200, 200)
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 238
        text: "Authorize this simulation"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 27; font.weight: Font.DemiBold
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 282
        text: "Fresh password authorizes this action once"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
    }
    Rectangle {
        x: 174; y: 314; width: 166; height: 28; radius: 8
        color: "#28194F"; border.width: 1; border.color: Design.purple
        Text {
            anchors.centerIn: parent; text: "BASE  ·  1 USDC  ·  SIMULATED"
            color: Design.purpleBright; font.family: Design.fontFamily
            font.pixelSize: 8; font.weight: Font.Bold; font.letterSpacing: 0.35
        }
    }
    PasswordInput {
        id: passwordField; objectName: "mockPasswordField"
        fieldObjectName: "mockPasswordTextInput"
        x: 86; y: 370; width: 342; height: 61
        placeholderText: "Enter wallet password"; onAccepted: root.submit()
    }
    FormButton {
        objectName: "mockAuthorizeButton"
        x: 86; y: 462; width: 342; height: 58
        label: "Authorize simulation"
        controlEnabled: passwordField.text.length >= 4
        onTriggered: root.submit()
    }
    FormButton {
        objectName: "mockCancelButton"
        x: 86; y: 543; width: 342; height: 54
        label: "Cancel"; controlEnabled: true; primary: false
        onTriggered: walletController.cancelMockAction()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 624
        text: "No transaction will be signed or sent"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
    }
}
