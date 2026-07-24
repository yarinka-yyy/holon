import QtQuick
import "."

PageState {
    id: root
    property var routes: walletController.trustedDraftRoutes
    property bool applyMode: walletController.trustedApplyMode

    ScreenHeader {
        objectName: "trustedReviewHeader"; x: 28; y: 54; width: 458
        title: root.applyMode ? "Apply Policy Draft" : "Review Policy Draft"
        subtitle: root.applyMode ? walletController.trustedActiveRevision : "Authority remains disabled"
        onBackRequested: root.applyMode
            ? walletController.closeTrustedApplyReview()
            : walletController.closeTrustedDraftReview()
    }
    Rectangle {
        x: 28; y: 136; width: 458; height: 72; radius: Design.controlRadius
        color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Text {
            x: 16; width: 426; anchors.verticalCenter: parent.verticalCenter
            text: root.applyMode
                ? "Guard will independently verify and pin this exact saved draft. Transfer authority will remain disabled."
                : "Saving records this draft only. Guard and Send will continue using the current disabled policy."
            wrapMode: Text.Wrap; color: Design.warning
            font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
    Text {
        visible: root.routes.length === 0; x: 52; y: 350; width: 410
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        text: root.applyMode
            ? "This will apply an empty disabled policy with no trusted recipients."
            : "This will save an empty draft with no trusted recipients."
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 14
    }
    ListView {
        id: reviewList; objectName: "trustedReviewList"
        x: 28; y: 232; width: 458; height: 400; clip: true; spacing: 10
        model: root.routes; boundsBehavior: Flickable.StopAtBounds
        delegate: SurfaceCard {
            required property var modelData
            width: reviewList.width; height: 116
            Text { x: 16; y: 12; text: modelData.networkLabel + " · " + modelData.assetLabel; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.DemiBold }
            Text { x: 16; y: 39; text: "Route ≤ " + modelData.routeAmount + " " + modelData.assetLabel + "  " + modelData.routeAmountUsd; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
            Text { x: 16; y: 63; text: "Fee ≤ " + modelData.feeAmount + " ETH  " + modelData.feeUsd; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
            Text { x: 16; y: 87; text: modelData.recipientCount + (modelData.recipientCount === 1 ? " exact recipient" : " exact recipients"); color: Design.accent; font.family: Design.fontFamily; font.pixelSize: 11 }
        }
    }
    FormButton {
        objectName: "trustedReviewContinueButton"; x: 72; y: 666; width: 370; height: 56
        label: "Confirm with Wallet Password"
        onTriggered: root.applyMode
            ? walletController.beginTrustedApplyPassword()
            : walletController.beginTrustedDraftPassword()
    }
    Text {
        x: 72; y: 742; width: 370; horizontalAlignment: Text.AlignHCenter
        text: root.applyMode
            ? "Only revision and digest metadata is sent to Guard"
            : "No address, cap or price is sent to Hermes"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
