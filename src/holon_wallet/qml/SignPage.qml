import QtQuick
import "."

TransactionFlowShell {
    id: root
    title: "Confirm Transaction"
    subtitle: action.actionType === "lending"
        ? "Authorize this exact Aave action once" : "Authorize this exact transfer once"
    activeStep: 1; onBackRequested: walletController.cancelMainnetExecution()
    property var action: walletController.transferAction
    property bool isLending: action.actionType === "lending"
    property string lendingMethod: action.method === "withdraw" && action.amountMode === "all"
        ? "withdraw all" : (action.method || "action")
    property url assetIcon: action.assetId === "eth"
        ? "assets/ethereum.svg" : "assets/usdc.png"
    property bool readyToSign: passwordField.text.length >= 4
        && walletController.mainnetExecutionAvailable

    function actionTitle() {
        if (!root.isLending) return (root.action.amount || "Transfer") + " on " + (root.action.network || "")
        if (root.action.method === "approve") return "Approve Aave V3"
        if (root.action.method === "supply") return "Supply to Aave V3"
        if (root.action.amountMode === "all") return "Withdraw all from Aave V3"
        return "Withdraw from Aave V3"
    }
    function actionSubtitle() {
        if (!root.isLending) return "To " + (root.action.recipient || "")
        if (root.action.method === "approve") return "Exact allowance · " + (root.action.amount || "USDC")
        if (root.action.method === "supply") return "Supply " + (root.action.amount || "USDC") + " from this Wallet"
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
        x: 0; y: 0; width: 458; height: 168; clip: true
        Image {
            visible: root.isLending; x: 0; y: 0; width: parent.width; height: 62
            source: "assets/aave-banner.png"; fillMode: Image.PreserveAspectCrop
            sourceSize: Qt.size(916, 124)
        }
        Text {
            x: 18; y: root.isLending ? 78 : 18; width: 350
            text: root.actionTitle(); color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 19; font.weight: Font.DemiBold
        }
        Text {
            x: 18; y: root.isLending ? 108 : 54; width: 350
            text: root.actionSubtitle(); color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 11
            elide: Text.ElideMiddle
        }
        Text {
            x: 18; y: 140
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
        x: 0; y: 190; text: "Wallet password"; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 13
    }
    PasswordInput {
        id: passwordField; objectName: "mainnetPasswordField"
        fieldObjectName: "mainnetPasswordInput"
        x: 0; y: 218; width: 458; height: 56
        placeholderText: "Enter fresh password"
    }
    Text {
        x: 0; y: 287; width: 458; horizontalAlignment: Text.AlignHCenter
        text: "The password is used once and is not stored"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
    Text {
        x: 18; y: 330; width: 422; horizontalAlignment: Text.AlignHCenter
        text: "The button below authorizes only the exact action shown above."
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        wrapMode: Text.Wrap
    }
    FormButton {
        objectName: "mainnetSendButton"; x: 0; y: 474; width: 458; height: 56
        label: root.isLending
            ? "Sign and submit " + root.lendingMethod
            : "Sign and send " + (root.action.token || "asset")
        controlEnabled: root.readyToSign
        onTriggered: root.submit()
    }
    FormButton {
        objectName: "mainnetCancelButton"; x: 0; y: 542; width: 458; height: 42
        label: "Cancel"; primary: false
        onTriggered: walletController.cancelMainnetExecution()
    }
}
