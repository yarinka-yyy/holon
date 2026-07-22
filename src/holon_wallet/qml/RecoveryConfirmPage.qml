import QtQuick
import "."

PageState {
    id: root
    property var action: walletController.recoveryAction
    property bool explicitlyConfirmed: false
    function submit() {
        walletController.submitRecovery(passwordField.text, explicitlyConfirmed)
    }
    onEnabledChanged: if (!enabled) {
        passwordField.clear(); explicitlyConfirmed = false
    }

    ScreenHeader {
        objectName: "recoveryConfirmHeader"; x: 28; y: 54; width: 458
        title: "Confirm Reveal"; subtitle: "Fresh authentication · one action"
        onBackRequested: walletController.editRecovery()
    }
    SurfaceCard {
        x: 28; y: 142; width: 458; height: 214
        Text {
            x: 18; y: 18; text: root.action.materialLabel || "Recovery Material"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 18; font.weight: Font.DemiBold
        }
        Text {
            x: 18; y: 55; text: root.action.accountLabel || ""; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 14
        }
        Text {
            x: 18; y: 84; width: parent.width - 36; text: root.action.address || ""
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
            wrapMode: Text.WrapAnywhere
        }
        Rectangle { x: 18; y: 130; width: parent.width - 36; height: 1; color: Design.border }
        Text {
            x: 18; y: 148; width: parent.width - 36
            text: root.action.derivationPath
                ? "Derivation path  " + root.action.derivationPath
                : "Local encrypted Account material"
            color: Design.accent; font.family: Design.fontFamily; font.pixelSize: 12
        }
        Text {
            x: 18; y: 180; width: parent.width - 36
            text: "Action " + (root.action.actionId || "")
            color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
            elide: Text.ElideMiddle
        }
    }
    PasswordInput {
        id: passwordField; objectName: "recoveryPasswordField"
        fieldObjectName: "recoveryPasswordInput"
        x: 72; y: 388; width: 370; height: 56
        placeholderText: "Enter fresh Wallet password"; onAccepted: root.submit()
    }
    Item {
        id: confirmCheck; objectName: "recoveryConfirmCheckbox"
        x: 72; y: 468; width: 370; height: 72
        function trigger() { root.explicitlyConfirmed = !root.explicitlyConfirmed }
        Rectangle {
            x: 0; y: 2; width: 24; height: 24; radius: 7
            color: root.explicitlyConfirmed ? Design.accent : Design.surfaceSecondary
            border.width: 1; border.color: root.explicitlyConfirmed ? Design.accent : Design.borderStrong
            Image {
                anchors.centerIn: parent; width: 18; height: 18
                visible: root.explicitlyConfirmed; source: "assets/check.svg"; sourceSize: Qt.size(36, 36)
            }
        }
        Text {
            x: 38; y: 0; width: 332; wrapMode: Text.Wrap
            text: "I understand that anyone who sees this "
                + (root.action.materialLabel || "material")
                + " can control this Account."
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
        }
        MouseArea {
            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
            onClicked: confirmCheck.trigger()
        }
    }
    Text {
        x: 72; y: 552; width: 370; horizontalAlignment: Text.AlignHCenter
        text: walletController.errorMessage; color: Design.danger
        font.family: Design.fontFamily; font.pixelSize: 12; wrapMode: Text.Wrap
    }
    FormButton {
        objectName: "recoveryRevealButton"; x: 72; y: 606; width: 370; height: 56
        label: "Reveal " + (root.action.materialLabel || "Material")
        controlEnabled: passwordField.text.length >= 4 && root.explicitlyConfirmed
        onTriggered: root.submit()
    }
    Text {
        x: 72; y: 680; width: 370; horizontalAlignment: Text.AlignHCenter
        text: "The material hides after 60 seconds or focus loss"
        color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
