import QtQuick
import QtQuick.Controls
import "."

TransactionFlowShell {
    id: root
    title: "Confirm Protected Action"
    subtitle: "Review every phase before one-time authorization"
    activeStep: 0
    onBackRequested: walletController.cancelPerpDexAction()
    property var action: walletController.perpDexAction

    function phaseSummary(phase) {
        let value = phase.semantic || {}
        if (phase.phaseType === "SET_REFERRER") return "Assign referral code " + value.code
        if (phase.phaseType === "SET_ISOLATED_LEVERAGE")
            return value.market + " · " + (value.is_cross ? "cross" : "isolated")
                + " · " + value.leverage + "x"
        if (phase.phaseType === "CANCEL_MARKET_ORDERS")
            return value.market + " · cancel " + (value.order_ids || []).length + " order(s)"
        if (phase.phaseType === "PLACE_IOC_ORDER")
            return value.market + " · " + (value.is_buy ? "BUY" : "SELL")
                + " " + value.size_asset + " · limit " + value.limit_price
                + (value.reduce_only ? " · reduce-only" : "")
        return (value.is_deposit ? "Deposit " : "Withdraw ")
            + value.amount_usdc + " USDC · official HLP"
    }

    Rectangle {
        x: 0; y: 0; width: 458; height: 42; radius: 12
        color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Text {
            anchors.centerIn: parent; text: "EXTERNAL PROTOCOL · REAL FUNDS"
            color: Design.warning; font.family: Design.fontFamily
            font.pixelSize: 11; font.weight: Font.DemiBold
        }
    }
    SurfaceCard {
        x: 0; y: 54; width: 458; height: 76
        Text {
            x: 16; y: 13; width: 426
            text: root.action.actionType || "Protected action"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 18; font.weight: Font.DemiBold
        }
        Text {
            x: 16; y: 44; width: 426; elide: Text.ElideMiddle
            text: root.action.account || ""
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    Flickable {
        id: phaseScroll; x: 0; y: 142; width: 458; height: 286
        clip: true; contentWidth: width
        contentHeight: phaseColumn.height
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        Column {
            id: phaseColumn; width: 458; spacing: 10
            Repeater {
                model: root.action.phases || []
                delegate: SurfaceCard {
                    required property var modelData
                    required property int index
                    width: 458; height: 70
                    Text {
                        x: 14; y: 10; width: 42; text: String(index + 1)
                        color: Design.accent; font.family: Design.fontFamily
                        font.pixelSize: 14; font.weight: Font.DemiBold
                    }
                    Text {
                        x: 48; y: 9; width: 394; text: modelData.phaseType
                        color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
                    }
                    Text {
                        x: 48; y: 35; width: 394; elide: Text.ElideRight
                        text: root.phaseSummary(modelData)
                        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
                    }
                }
            }
            Rectangle {
                visible: !!root.action.intent && root.action.intent.margin_mode === "CROSS"
                width: 458; height: visible ? 78 : 0; radius: 12
                color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
                Text {
                    anchors.fill: parent; anchors.margins: 14; wrapMode: Text.Wrap
                    text: "Cross margin shares PerpDEX collateral with other cross positions. This operation can expose account-wide collateral to liquidation risk."
                    color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 11
                }
            }
            Rectangle {
                visible: (root.action.disclosure || "").length > 0
                width: 458; height: visible ? 118 : 0; radius: 12
                color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
                Text {
                    anchors.fill: parent; anchors.margins: 14; wrapMode: Text.Wrap
                    text: root.action.disclosure || ""
                    color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 11
                }
            }
            Rectangle {
                visible: root.action.actionType === "HLP_DEPOSIT"
                width: 458; height: visible ? 72 : 0; radius: 12
                color: Design.surface; border.width: 1; border.color: Design.border
                Text {
                    anchors.fill: parent; anchors.margins: 14; wrapMode: Text.Wrap
                    text: "HLP has a four-day lock-up. A new deposit restarts the lock period."
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
                }
            }
        }
    }
    FormButton {
        objectName: "perpDexContinueButton"; x: 0; y: 466; width: 458; height: 54
        label: "Continue"; onTriggered: walletController.beginPerpDexExecution()
    }
    FormButton {
        objectName: "perpDexCancelReviewButton"; x: 0; y: 532; width: 458; height: 48
        label: "Cancel entire operation"; primary: false
        onTriggered: walletController.cancelPerpDexAction()
    }
}
