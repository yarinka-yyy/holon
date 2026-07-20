import QtQuick
import QtQuick.Controls
import "."

// qmllint disable unqualified

Item {
    id: root
    property string selectedType: walletController.importPrivateOnly ? "private" : "seed"

    function submit() {
        var value = root.selectedType === "seed" ? seedArea.text : privateField.text
        walletController.submitImport(root.selectedType, value)
    }
    onEnabledChanged: {
        seedArea.clear()
        privateField.clear()
        root.selectedType = walletController.importPrivateOnly ? "private" : "seed"
    }

    BackButton {
        objectName: "importBackButton"; x: 22; y: 42
        onTriggered: walletController.cancelFlow()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 49
        text: walletController.importPrivateOnly ? "Import Private Key" : "Import Account"
        color: Design.text; font.family: Design.fontFamily
        font.pixelSize: 25; font.weight: Font.Bold
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 99
        text: root.selectedType === "seed"
            ? "Enter a valid 12 or 24 word BIP39 seed phrase"
            : "Enter a raw EVM private key"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
    }

    Item {
        x: 86; y: 139; width: 342; height: 43
        visible: !walletController.importPrivateOnly
        Rectangle {
            anchors.fill: parent; radius: 11
            color: Design.surface; border.width: 1; border.color: Design.border
        }
        Rectangle {
            x: root.selectedType === "seed" ? 3 : 172
            y: 3; width: 167; height: 37; radius: 9
            color: "#312052"; border.width: 1; border.color: Design.purple
            Behavior on x { NumberAnimation { duration: Design.normalMotion; easing.type: Easing.OutCubic } }
        }
        Text {
            x: 0; width: 171; anchors.verticalCenter: parent.verticalCenter
            horizontalAlignment: Text.AlignHCenter; text: "Seed Phrase"
            color: root.selectedType === "seed" ? Design.text : Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 12
        }
        Text {
            x: 171; width: 171; anchors.verticalCenter: parent.verticalCenter
            horizontalAlignment: Text.AlignHCenter; text: "Private Key"
            color: root.selectedType === "private" ? Design.text : Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 12
        }
        MouseArea { x: 0; width: 171; height: parent.height; onClicked: root.selectedType = "seed" }
        MouseArea { x: 171; width: 171; height: parent.height; onClicked: root.selectedType = "private" }
    }

    Rectangle {
        id: secretCard
        x: 54; y: walletController.importPrivateOnly ? 174 : 201
        width: 406; height: 205; radius: 14
        color: "#780B1225"; border.width: 1.3; border.color: Design.purple
        GlowWave { x: 170; y: 145; width: 236; height: 60; opacity: 0.35 }
        ScrollView {
            anchors.fill: parent; anchors.margins: 18
            visible: root.selectedType === "seed"
            clip: true
            TextArea {
                id: seedArea; objectName: "seedPhraseInput"
                wrapMode: TextEdit.Wrap; color: Design.text
                selectionColor: Design.purple; selectedTextColor: "white"
                placeholderText: "Enter your seed phrase"
                placeholderTextColor: Design.textFaint
                font.family: Design.fontFamily; font.pixelSize: 14
                background: null
            }
        }
        PasswordInput {
            id: privateField; objectName: "privateKeyField"
            fieldObjectName: "privateKeyInput"
            anchors.left: parent.left; anchors.right: parent.right
            anchors.leftMargin: 18; anchors.rightMargin: 18
            anchors.verticalCenter: parent.verticalCenter
            height: 62; visible: root.selectedType === "private"
            placeholderText: "Enter your private key"
            onAccepted: root.submit()
        }
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 424
        text: walletController.errorMessage; color: "#FF7F9B"
        font.family: Design.fontFamily; font.pixelSize: 11
    }
    FormButton {
        objectName: "importContinueButton"
        x: 86; y: 458; width: 342; height: 58; label: "Continue"
        controlEnabled: root.selectedType === "seed"
            ? seedArea.text.trim().length > 0 : privateField.text.trim().length > 0
        onTriggered: root.submit()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 540
        text: "Input is validated locally and never leaves Wallet"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
    }
}
