import QtQuick
import QtQuick.Controls
import "."

PageState {
    id: root
    property string selectedType: walletController.importPrivateOnly ? "private" : "seed"
    function submit() {
        walletController.submitImport(selectedType,
            selectedType === "seed" ? seedArea.text : privateField.text)
    }
    onEnabledChanged: {
        seedArea.clear(); privateField.clear()
        selectedType = walletController.importPrivateOnly ? "private" : "seed"
    }
    ScreenHeader {
        objectName: "import"; x: 28; y: 54; width: 458
        title: walletController.importPrivateOnly ? "Import Private Key" : "Import Account"
        subtitle: "Validated locally before encrypted storage"
        onBackRequested: walletController.cancelFlow()
    }
    Row {
        visible: !walletController.importPrivateOnly; x: 72; y: 150; spacing: 8
        NetworkCard {
            width: 181; height: 44; label: "Seed Phrase"; iconSource: "assets/lock.svg"
            selected: root.selectedType === "seed"
            onTriggered: root.selectedType = "seed"
        }
        NetworkCard {
            width: 181; height: 44; label: "Private Key"; iconSource: "assets/user.svg"
            selected: root.selectedType === "private"
            onTriggered: root.selectedType = "private"
        }
    }
    Text {
        x: 72; y: walletController.importPrivateOnly ? 174 : 222
        text: root.selectedType === "seed" ? "12 or 24 English BIP39 words" : "32-byte EVM private key"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
    }
    SurfaceCard {
        x: 72; y: walletController.importPrivateOnly ? 210 : 258
        width: 370; height: 230
        ScrollView {
            anchors.fill: parent; anchors.margins: 16
            visible: root.selectedType === "seed"; clip: true
            TextArea {
                id: seedArea; objectName: "seedPhraseInput"
                wrapMode: TextEdit.Wrap; color: Design.text
                selectionColor: Design.accent; selectedTextColor: Design.textOnAccent
                placeholderText: "Enter seed phrase"
                placeholderTextColor: Design.textFaint
                font.family: Design.fontFamily; font.pixelSize: 15; background: null
            }
        }
        PasswordInput {
            id: privateField; objectName: "privateKeyField"
            fieldObjectName: "privateKeyInput"
            x: 16; width: parent.width - 32; height: 56
            anchors.verticalCenter: parent.verticalCenter
            visible: root.selectedType === "private"
            placeholderText: "Enter private key"; onAccepted: root.submit()
        }
    }
    Text {
        x: 72; y: walletController.importPrivateOnly ? 458 : 506
        width: 370; horizontalAlignment: Text.AlignHCenter
        text: walletController.errorMessage; color: Design.danger
        font.family: Design.fontFamily; font.pixelSize: 12
    }
    FormButton {
        objectName: "importContinueButton"; x: 72; width: 370; height: 56
        y: walletController.importPrivateOnly ? 500 : 548; label: "Continue"
        controlEnabled: root.selectedType === "seed"
            ? seedArea.text.trim().length > 0 : privateField.text.trim().length > 0
        onTriggered: root.submit()
    }
    Row {
        anchors.horizontalCenter: parent.horizontalCenter; y: 636; spacing: 8
        Image { width: 18; height: 18; source: "assets/lock.svg"; sourceSize: Qt.size(36, 36) }
        Text {
            text: "Input never leaves Holon Wallet"; color: Design.textFaint
            font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
}
