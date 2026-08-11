import QtQuick
import "."

TransactionFlowShell {
    id: root
    property bool isFunding: root.action.actionType === "FUND_TRADING_ACCOUNT"
    title: isFunding ? "Authorize Hyperliquid deposit" : "Authorize Protected Action"
    subtitle: isFunding
        ? "Your password signs one exact Arbitrum deposit"
        : "One fresh password authorizes the exact bundle once"
    activeStep: 1
    onBackRequested: walletController.returnToPerpDexReview()
    property var action: walletController.perpDexAction
    property bool ready: passwordField.text.length >= 4

    function fundingAmount() {
        let phases = root.action.phases || []
        return phases.length > 0 ? (phases[0].semantic || {}).amount_usdc || "" : ""
    }

    function submit() {
        if (!ready) return
        let oneTimePassword = passwordField.text
        passwordField.clear()
        walletController.submitPerpDexExecution(oneTimePassword)
        oneTimePassword = ""
    }
    onEnabledChanged: if (!enabled) passwordField.clear()

    SurfaceCard {
        x: 0; y: 18; width: 458; height: 150
        Text {
            x: 18; y: 20; width: 422
            text: root.isFunding
                ? "Deposit " + root.fundingAmount() + " USDC to Hyperliquid"
                : root.action.actionType || "Protected action"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 20; font.weight: Font.DemiBold
        }
        Text {
            x: 18; y: 60; width: 422
            text: root.isFunding
                ? "One unsigned transaction · Arbitrum One"
                : (root.action.phases || []).length + " sequential phase(s) · no automatic retry"
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        }
        Text {
            x: 18; y: 96; width: 422; wrapMode: Text.Wrap
            text: root.isFunding
                ? "Signing sends this deposit once to the official Hyperliquid Bridge2. Credit can take time."
                : "If any phase fails or becomes uncertain, every later phase stops."
            color: root.isFunding ? Design.textMuted : Design.warning
            font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    Text {
        x: 0; y: 324; text: "Wallet password"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
    }
    PasswordInput {
        id: passwordField; objectName: "perpDexPasswordInput"
        x: 0; y: 350; width: 458; height: 56
        placeholderText: "Enter fresh password"
    }
    Text {
        x: 0; y: 418; width: 458; horizontalAlignment: Text.AlignHCenter
        text: "Password and signatures are never stored or sent to Hermes"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
    FormButton {
        objectName: "perpDexSubmitButton"; x: 0; y: 466; width: 458; height: 56
        label: root.isFunding
            ? "Sign and submit " + root.fundingAmount() + " USDC deposit"
            : "Sign and submit exact bundle"
        controlEnabled: root.ready
        onTriggered: root.submit()
    }
    FormButton {
        objectName: "perpDexCancelPasswordButton"; x: 0; y: 534; width: 458; height: 42
        label: "Cancel"; primary: false
        onTriggered: walletController.cancelPerpDexAction()
    }
}
