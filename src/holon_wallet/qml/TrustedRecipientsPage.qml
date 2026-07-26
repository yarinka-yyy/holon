import QtQuick
import "."

PageState {
    id: root
    property var routes: walletController.trustedDraftRoutes

    ScreenHeader {
        objectName: "trustedRecipientsHeader"; x: 28; y: 54; width: 458
        title: "Trusted Recipients"; subtitle: "Non-authoritative policy draft"
        onBackRequested: walletController.closeTrustedRecipients()
    }
    Rectangle {
        x: 28; y: 136; width: 458; height: 78; radius: Design.controlRadius
        color: walletController.trustedDraftAvailable ? Design.accentSoft : "#33D65C5C"
        border.width: 1
        border.color: walletController.trustedDraftAvailable ? Design.accent : Design.danger
        Text {
            objectName: "trustedDraftStatus"; x: 16; width: 426
            anchors.verticalCenter: parent.verticalCenter; wrapMode: Text.Wrap
            text: walletController.trustedDraftStatus + "\n"
                + walletController.trustedActiveRevision
                + (walletController.trustedDraftMatchesActive ? " · draft matches" : " · draft differs")
                + " · " + walletController.trustedAuthorityStatus
                + "\n" + walletController.trustedAuthorityState
            color: walletController.trustedDraftAvailable ? Design.text : Design.danger
            font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
    SurfaceCard {
        x: 28; y: 230; width: 458; height: 142
        Text { x: 16; y: 12; text: "AAVE · BASE · USDC"; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 14; font.weight: Font.DemiBold }
        DraftField {
            id: lendingAmount; x: 16; y: 38; width: 170; height: 72
            label: walletController.trustedLendingLimits.withdrawOnly
                ? "Withdraw cap (USDC)" : "Legacy supply cap (USDC)"
            placeholderText: "1.01"
            text: walletController.trustedLendingLimits.amount
            fieldObjectName: "trustedLendingAmountInput"
        }
        DraftField {
            id: lendingFee; x: 196; y: 38; width: 150; height: 72
            label: "Fee cap (ETH)"; placeholderText: "0.0001"
            text: walletController.trustedLendingLimits.fee
            fieldObjectName: "trustedLendingFeeInput"
        }
        FormButton {
            objectName: "trustedSaveLendingButton"; x: 354; y: 58; width: 88; height: 48
            label: "Set Limits"; primary: false
            onTriggered: walletController.saveTrustedLendingLimits(lendingAmount.text, lendingFee.text)
        }
        Text {
            x: 16; y: 116
            text: walletController.trustedLendingLimits.withdrawOnly
                ? "Only exact or all-position withdraw · Send remains separate"
                : "Legacy approve/supply draft · Set Limits to replace it with withdraw-only"
            color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
        }
    }
    Text {
        x: 28; y: 392; text: "TRANSFER ROUTES"; color: Design.textFaint
        font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
    }
    Text {
        visible: root.routes.length === 0; x: 52; y: 475; width: 410
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        text: walletController.trustedDraftAvailable
            ? "No routes in this draft. Add one of the supported Ethereum or Base routes."
            : "Public Wallet functions remain available. This draft cannot enable transfers."
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 14
    }
    ListView {
        id: routeList; objectName: "trustedRouteList"
        x: 28; y: 416; width: 458; height: 178; clip: true; spacing: 10
        model: root.routes; boundsBehavior: Flickable.StopAtBounds
        delegate: SurfaceCard {
            required property var modelData
            objectName: "trustedRoute_" + modelData.networkId + "_" + modelData.assetId
            width: routeList.width; height: 96
            Text {
                x: 16; y: 14; text: modelData.networkLabel + " · " + modelData.assetLabel
                color: Design.text; font.family: Design.fontFamily
                font.pixelSize: 16; font.weight: Font.DemiBold
            }
            Text {
                x: 16; y: 43
                text: "Per transfer ≤ " + modelData.routeAmount + " " + modelData.assetLabel
                    + "  ·  " + modelData.routeAmountUsd
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
            }
            Text {
                x: 16; y: 67
                text: modelData.recipientCount + (modelData.recipientCount === 1 ? " recipient" : " recipients")
                color: modelData.recipientCount > 0 ? Design.accent : Design.warning
                font.family: Design.fontFamily; font.pixelSize: 11
            }
            MouseArea {
                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                onClicked: walletController.editTrustedRoute(modelData.networkId, modelData.assetId)
            }
        }
    }
    FormButton {
        objectName: "trustedAddRouteButton"; x: 28; y: 620; width: 140; height: 52
        label: "Add Route"; primary: false
        controlEnabled: walletController.trustedDraftAvailable && root.routes.length < 4
        onTriggered: walletController.beginTrustedRoute()
    }
    FormButton {
        objectName: "trustedReviewButton"; x: 178; y: 620; width: 140; height: 52
        label: "Save Draft"
        controlEnabled: walletController.trustedDraftAvailable
        onTriggered: walletController.showTrustedDraftReview()
    }
    FormButton {
        objectName: "trustedApplyButton"; x: 328; y: 620; width: 158; height: 52
        label: walletController.trustedCanInitializeAuthority
            ? "Initialize Authority" : "Apply Draft"
        controlEnabled: walletController.trustedCanInitializeAuthority
            || walletController.trustedCanApply
        onTriggered: walletController.trustedCanInitializeAuthority
            ? walletController.showTrustedInitializationReview()
            : walletController.showTrustedApplyReview()
    }
    FormButton {
        objectName: "trustedActivateLendingButton"; x: 28; y: 684; width: 220; height: 48
        label: walletController.trustedLendingLimits.enabled ? "Lending Active" : "Activate Lending"
        controlEnabled: walletController.trustedCanActivateLending
        onTriggered: walletController.showTrustedCapabilityReview(true)
    }
    FormButton {
        objectName: "trustedDeactivateLendingButton"; x: 266; y: 684; width: 220; height: 48
        label: "Deactivate Lending"; primary: false
        controlEnabled: walletController.trustedCanDeactivateLending
        onTriggered: walletController.showTrustedCapabilityReview(false)
    }
    Text {
        objectName: "trustedDraftError"; x: 48; y: 742; width: 418
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        text: walletController.errorMessage; color: Design.danger
        font.family: Design.fontFamily; font.pixelSize: 12
    }
    Text {
        x: 48; y: 786; width: 418; horizontalAlignment: Text.AlignHCenter
        text: "Draft limits do not authorize signing until Guard activation"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
