import QtQuick
import "."

TransactionFlowShell {
    id: root
    property var action: walletController.perpDexAction
    property var presentation: action.presentation || ({})
    title: presentation.label || "Review action"
    subtitle: "Review this one-time action before signing"
    activeStep: 0
    onBackRequested: walletController.cancelPerpDexAction()

    Rectangle {
        x: 0; y: 0; width: 458; height: 38; radius: 11
        color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Text {
            anchors.centerIn: parent; text: "External protocol · real funds"
            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold
        }
    }
    SurfaceCard {
        x: 0; y: 50; width: 458; height: 118
        Text {
            x: 16; y: 15; width: 426; text: presentation.title || "Protected action"
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 20; font.weight: Font.DemiBold
        }
        Text {
            x: 16; y: 47; width: 426; text: presentation.subtitle || "Wallet confirmation required"
            color: Design.accent; font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
        }
        Text {
            x: 16; y: 76; width: 426; wrapMode: Text.Wrap
            text: action.actionType === "FUND_TRADING_ACCOUNT"
                ? "You are depositing native USDC from your Arbitrum wallet into your Hyperliquid trading balance."
                : action.actionType === "OPEN_POSITION"
                    ? "Leverage can lead to liquidation. Check the price limit and position size before continuing."
                    : action.actionType === "CLOSE_POSITION"
                        ? "Reduce-only prevents this order from increasing or reversing your position."
                        : "The Wallet will re-check live conditions before it asks for your local password."
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    Flickable {
        id: actionScroll; x: 0; y: 180; width: 458; height: 270; clip: true
        contentWidth: width; contentHeight: actionColumn.height; boundsBehavior: Flickable.StopAtBounds
        Column {
            id: actionColumn; width: 458; spacing: 5
            Repeater {
                model: presentation.summaryRows || []
                delegate: SurfaceCard {
                    required property var modelData
                    width: 458; height: 40
                    Text {
                        x: 15; anchors.verticalCenter: parent.verticalCenter; width: 180
                        text: modelData.label || ""; color: Design.textMuted
                        font.family: Design.fontFamily; font.pixelSize: 12
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 15; anchors.verticalCenter: parent.verticalCenter; width: 240
                        text: modelData.value || ""; horizontalAlignment: Text.AlignRight; elide: Text.ElideRight
                        color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12; font.weight: Font.Medium
                    }
                }
            }
            Repeater {
                model: presentation.warnings || []
                delegate: Rectangle {
                    required property string modelData
                    width: 458; height: warningText.implicitHeight + 24; radius: 11
                    color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
                    Text {
                        id: warningText; x: 14; y: 12; width: 430; wrapMode: Text.Wrap
                        text: modelData; color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 11
                    }
                }
            }
            PerpDexTechnicalDetails { width: 458; details: presentation.technicalDetails || [] }
        }
    }
    ScrollCue {
        objectName: "perpDexReviewScrollCue"; anchors.right: actionScroll.right; anchors.rightMargin: 8
        anchors.bottom: actionScroll.bottom; anchors.bottomMargin: 8
        suggested: actionScroll.contentHeight > actionScroll.height && actionScroll.contentY < actionScroll.contentHeight - actionScroll.height - 2
    }
    FormButton {
        objectName: "perpDexContinueButton"; x: 0; y: 466; width: 458; height: 54
        label: "Continue to local confirmation"; onTriggered: walletController.beginPerpDexExecution()
    }
    FormButton {
        objectName: "perpDexCancelReviewButton"; x: 0; y: 532; width: 458; height: 48
        label: "Cancel"; primary: false; onTriggered: walletController.cancelPerpDexAction()
    }
}
