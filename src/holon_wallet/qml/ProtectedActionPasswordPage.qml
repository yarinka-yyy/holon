import QtQuick
import "."

TransactionFlowShell {
    id: root
    property var action: walletController.perpDexAction
    property var presentation: action.presentation || ({})
    property bool ready: passwordField.text.length >= 4
    title: "Authorize action"
    subtitle: "Your fresh local password authorizes this exact action once"
    activeStep: 1
    onBackRequested: walletController.returnToPerpDexReview()

    function submit() {
        if (!ready) return
        let oneTimePassword = passwordField.text
        passwordField.clear()
        walletController.submitPerpDexExecution(oneTimePassword)
        oneTimePassword = ""
    }
    onEnabledChanged: if (!enabled) passwordField.clear()

    SurfaceCard {
        x: 0; y: 18; width: 458; height: 136
        Text {
            x: 18; y: 19; width: 422; text: presentation.title || "Protected action"
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 20; font.weight: Font.DemiBold
        }
        Text {
            x: 18; y: 53; width: 422; text: presentation.subtitle || "One-time authorization"
            color: Design.accent; font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
        }
        Text {
            x: 18; y: 82; width: 422; wrapMode: Text.Wrap
            text: action.actionType === "FUND_TRADING_ACCOUNT"
                ? "Signing sends this deposit once. Hyperliquid updates your trading balance separately."
                : "If a check fails or becomes uncertain, later steps stop. Nothing is retried automatically."
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    Text {
        x: 0; y: 324; text: "Wallet password"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
    }
    PasswordInput {
        id: passwordField; objectName: "perpDexPasswordInput"
        x: 0; y: 350; width: 458; height: 56; placeholderText: "Enter fresh password"
    }
    Text {
        x: 0; y: 418; width: 458; horizontalAlignment: Text.AlignHCenter
        text: "Your password and signature stay in the Wallet, never in Hermes"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
    FormButton {
        objectName: "perpDexSubmitButton"; x: 0; y: 466; width: 458; height: 56
        label: action.actionType === "FUND_TRADING_ACCOUNT" ? "Sign and send deposit" : "Sign and submit order"
        controlEnabled: root.ready; onTriggered: root.submit()
    }
    FormButton {
        objectName: "perpDexCancelPasswordButton"; x: 0; y: 534; width: 458; height: 42
        label: "Cancel"; primary: false; onTriggered: walletController.cancelPerpDexAction()
    }
}
