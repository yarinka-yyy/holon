import QtQuick
import "."

TransactionFlowShell {
    id: root
    title: "Confirm Transaction"
    subtitle: action.actionType === "lending"
        ? "Authorize this exact Lending action once" : "Authorize this exact transfer once"
    activeStep: 1; onBackRequested: walletController.returnToTransferReview()
    property var action: walletController.transferAction
    property bool isLending: action.actionType === "lending"
    property string lendingMethod: action.method === "withdraw" && action.amountMode === "all"
        ? "withdraw all" : (action.method || "action")
    property url assetIcon: action.assetId === "eth"
        ? "assets/ethereum.svg" : "assets/usdc.webp"
    property bool readyToSign: passwordField.text.length >= 4
        && walletController.mainnetExecutionAvailable

    function actionTitle() {
        if (!root.isLending) return (root.action.amount || "Transfer") + " on " + (root.action.network || "")
        if (root.action.method === "approve") return "Step 1 of 2 · Approve"
        if (root.action.method === "supply" || root.action.method === "deposit")
            return "Step 2 of 2 · Supply"
        if (root.action.amountMode === "all") return "Withdraw all"
        return "Withdraw"
    }
    function actionSubtitle() {
        if (!root.isLending) return "To " + (root.action.recipient || "")
        if (root.action.method === "approve") return "Exact allowance · " + (root.action.amount || "USDC")
        if (root.action.method === "supply" || root.action.method === "deposit")
            return "Supply " + (root.action.amount || "USDC") + " from this Wallet"
        return "Receiver · " + (root.action.accountLabel || "Active Wallet")
    }
    function submit() {
        if (!readyToSign) return
        let oneTimePassword = passwordField.text
        passwordField.clear()
        walletController.submitMainnetExecution(oneTimePassword)
        oneTimePassword = ""
    }
    onEnabledChanged: if (!enabled) passwordField.clear()

    SurfaceCard {
        x: 0; y: 0; width: 458; height: 128; clip: true
        Image {
            objectName: "mainnetSignProtocolLogo"
            visible: root.isLending; x: parent.width - 158; y: 18; width: 140; height: 28
            source: root.action.protocolLogo || ""; fillMode: Image.PreserveAspectFit
            sourceSize: Qt.size(280, 56)
        }
        Text {
            objectName: "mainnetSignActionTitle"
            x: 18; y: 18; width: root.isLending ? 270 : 350
            text: root.actionTitle(); color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 19; font.weight: Font.DemiBold
        }
        Text {
            x: 18; y: 58; width: 422
            text: root.actionSubtitle(); color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 11
            elide: Text.ElideMiddle
        }
        Text {
            x: 18; y: 94
            text: "Maximum fee " + walletController.transferFeeUsd
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
        Image {
            visible: !root.isLending
            anchors.right: parent.right; anchors.rightMargin: 18; y: 20
            width: 50; height: 50; source: root.assetIcon; sourceSize: Qt.size(100, 100)
        }
    }
    Text {
        x: 18; y: 154; width: 422; horizontalAlignment: Text.AlignHCenter
        text: "The button below authorizes only the exact action shown above."
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        wrapMode: Text.Wrap
    }
    Rectangle {
        objectName: "lendingSignHighFeeWarning"
        visible: root.isLending && walletController.lendingHighFeeWarning
        x: 0; y: 196; width: 458; height: 46
        radius: 10; color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Text {
            anchors.centerIn: parent; width: parent.width - 24
            text: "Base network fee is unusually high. Signing is still available."
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    Text {
        x: 0; y: 346; text: "Wallet password"; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 13
    }
    PasswordInput {
        id: passwordField; objectName: "mainnetPasswordField"
        fieldObjectName: "mainnetPasswordInput"
        x: 0; y: 370; width: 458; height: 56
        placeholderText: "Enter fresh password"
    }
    Text {
        x: 0; y: 436; width: 458; horizontalAlignment: Text.AlignHCenter
        text: "The password is used once and is not stored"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
    FormButton {
        objectName: "mainnetSendButton"; x: 0; y: 466; width: 458; height: 56
        label: root.isLending
            ? "Sign and submit " + root.lendingMethod
            : "Sign and send " + (root.action.token || "asset")
        controlEnabled: root.readyToSign
        onTriggered: root.submit()
    }
    FormButton {
        objectName: "mainnetCancelButton"; x: 0; y: 534; width: 458; height: 42
        label: "Cancel"; primary: false
        onTriggered: walletController.cancelMainnetExecution()
    }
}
