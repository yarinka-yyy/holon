import QtQuick
import QtQuick.Controls
import "."

TransactionFlowShell {
    id: root
    property bool isFunding: (root.result.actionType || "") === "FUND_TRADING_ACCOUNT"
    title: isFunding ? "Hyperliquid deposit result" : "Protected Action Result"
    subtitle: isFunding ? root.fundingSubtitle() : result.status === "PENDING_CREDIT"
        ? "Broadcast is not a Hyperliquid credit · refresh portfolio"
        : "Public reconciliation · no automatic retry"
    activeStep: 3; backVisible: false
    property var result: walletController.perpDexResult
    property bool positive: result.status === "COMPLETED" || result.status === "PENDING_CREDIT"

    function fundingSubtitle() {
        let code = root.result.code || ""
        if (code === "FUNDING_POLICY_UNAVAILABLE")
            return "Arbitrum is unavailable. Nothing was signed or sent."
        if (code === "FUNDING_GUARD_FEE_CAP_EXCEEDED" || code === "FUNDING_WALLET_FEE_CAP_EXCEEDED")
            return "The network fee changed beyond the reviewed limit. Nothing was signed or sent."
        if (code === "FUNDING_INSUFFICIENT_USDC")
            return "There is not enough native USDC for this deposit. Nothing was signed or sent."
        if (code === "FUNDING_INSUFFICIENT_ETH")
            return "There is not enough ETH for the Arbitrum network fee. Nothing was signed or sent."
        if (code === "FUNDING_ACCOUNT_CHANGED" || code === "FUNDING_AMOUNT_CHANGED"
                || code === "FUNDING_GUARD_ROUTE_CHANGED" || code === "FUNDING_WALLET_ROUTE_CHANGED")
            return "The protected deposit changed. Nothing was signed or sent."
        if (root.result.status === "PENDING_CREDIT")
            return "Submitted to Arbitrum · Hyperliquid credit is pending"
        if (root.result.status === "FAILED")
            return "The Wallet stopped this deposit before it could complete"
        if (root.result.status === "UNKNOWN")
            return "Submission status is uncertain · check the public portfolio"
        return "Public reconciliation · no automatic retry"
    }
    function fundingStatus() {
        if (root.result.status === "PENDING_CREDIT") return "AWAITING HYPERLIQUID CREDIT"
        if (root.result.status === "FAILED") return "DEPOSIT NOT COMPLETED"
        return root.result.status || "UNKNOWN"
    }
    function phaseTitle(phase) {
        return root.isFunding && phase.phaseType === "ARBITRUM_USDC_TRANSFER"
            ? "Hyperliquid USDC deposit" : phase.phaseType || "Phase"
    }
    function phaseState(phase) {
        return root.isFunding && phase.state === "PENDING_CREDIT" ? "SUBMITTED" : phase.state || "UNKNOWN"
    }
    function phaseDetail(phase) {
        if (root.isFunding && phase.state === "PENDING_CREDIT")
            return phase.publicId ? "Arbitrum transaction submitted · " + phase.publicId : "Arbitrum transaction submitted"
        if (root.isFunding && phase.state === "FAILED")
            return "No automatic retry was sent. You can create a new review after checking the issue."
        return (phase.code || "") + (phase.publicId ? " · " + phase.publicId : "")
    }

    SurfaceCard {
        x: 0; y: 0; width: 458; height: 154
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter; y: 18
            width: 60; height: 60; radius: 30
            color: root.positive ? Design.accentSoft : "#332C2020"
            border.width: 1; border.color: root.positive ? Design.accent : Design.warning
            Image {
                anchors.centerIn: parent; width: 32; height: 32
                source: root.positive ? "assets/check.svg" : "assets/warning.svg"
            }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; y: 92
            text: root.isFunding ? root.fundingStatus() : root.result.status || "UNKNOWN"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 19; font.weight: Font.DemiBold
        }
        Text {
            x: 20; y: 121; width: 418; horizontalAlignment: Text.AlignHCenter
            text: root.isFunding ? root.fundingSubtitle() : root.result.code || "PERPDEX_RESULT_UNKNOWN"
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    Flickable {
        x: 0; y: 170; width: 458; height: 288; clip: true
        contentWidth: width; contentHeight: phaseColumn.height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        Column {
            id: phaseColumn; width: 458; spacing: 9
            Repeater {
                model: root.result.phases || []
                delegate: SurfaceCard {
                    required property var modelData
                    width: 458; height: 66
                    Text {
                        x: 14; y: 10; width: 280; text: root.phaseTitle(modelData)
                        color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 14; y: 10
                        text: root.phaseState(modelData)
                        color: modelData.state === "CONFIRMED" ? Design.accent : Design.warning
                        font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold
                    }
                    Text {
                        x: 14; y: 36; width: 430; elide: Text.ElideMiddle
                        text: root.phaseDetail(modelData)
                        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10
                    }
                }
            }
        }
    }
    FormButton {
        objectName: "perpDexResultDoneButton"; x: 0; y: 528
        width: 458; height: 56; label: "Done"
        onTriggered: walletController.finishPerpDexExecution()
    }
}
