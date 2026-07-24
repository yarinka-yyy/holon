import QtQuick
import "."

PageState {
    id: root
    property var route: walletController.trustedRoute
    property var recipient: walletController.trustedRecipient
    function load() {
        labelField.text = recipient.label || ""
        addressField.text = recipient.address || ""
        amountField.text = recipient.maxAmount || ""
    }
    onActiveChanged: if (active) load()

    ScreenHeader {
        objectName: "trustedRecipientHeader"; x: 28; y: 54; width: 458
        title: recipient.isNew ? "Add Recipient" : "Edit Recipient"
        subtitle: route.networkLabel + " · " + route.assetLabel
        onBackRequested: walletController.closeTrustedRecipient()
    }
    Rectangle {
        x: 28; y: 138; width: 458; height: 66; radius: Design.controlRadius
        color: Design.accentSoft; border.width: 1; border.color: Design.accent
        Text {
            x: 16; width: 426; anchors.verticalCenter: parent.verticalCenter
            text: "The exact address is authoritative. The label and USD estimate are display-only."
            wrapMode: Text.Wrap; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
    DraftField {
        id: labelField; objectName: "trustedLabelField"; fieldObjectName: "trustedLabelInput"
        x: 28; y: 238; width: 458; height: 70
        label: "Local label"; placeholderText: "Savings"
    }
    DraftField {
        id: addressField; objectName: "trustedAddressField"; fieldObjectName: "trustedAddressInput"
        x: 28; y: 332; width: 458; height: 70
        label: "Exact EVM address"; placeholderText: "0x…"
    }
    DraftField {
        id: amountField; objectName: "trustedRecipientAmountField"; fieldObjectName: "trustedRecipientAmountInput"
        x: 28; y: 426; width: 458; height: 70
        label: "Max per transfer · " + route.assetLabel; placeholderText: "50"
    }
    Text {
        objectName: "trustedRecipientUsd"; x: 28; y: 505; width: 458
        text: walletController.trustedAmountUsd(route.assetId || "usdc", amountField.text)
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
    FormButton {
        objectName: "trustedSaveRecipientButton"; x: 28; y: 556; width: 458; height: 52
        label: recipient.isNew ? "Add to Draft" : "Update Recipient"
        onTriggered: walletController.saveTrustedRecipient(labelField.text, addressField.text, amountField.text)
    }
    FormButton {
        objectName: "trustedDeleteRecipientButton"; x: 28; y: 628; width: 458; height: 50
        label: "Remove Recipient"; primary: false; controlEnabled: !recipient.isNew
        onTriggered: walletController.deleteTrustedRecipient()
    }
    Text {
        objectName: "trustedRecipientError"; x: 48; y: 704; width: 418
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        text: walletController.errorMessage; color: Design.danger
        font.family: Design.fontFamily; font.pixelSize: 12
    }
}
