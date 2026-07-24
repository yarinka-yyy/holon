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
        x: 28; y: 136; width: 458; height: 62; radius: Design.controlRadius
        color: walletController.trustedDraftAvailable ? Design.accentSoft : "#33D65C5C"
        border.width: 1
        border.color: walletController.trustedDraftAvailable ? Design.accent : Design.danger
        Text {
            objectName: "trustedDraftStatus"; x: 16; width: 426
            anchors.verticalCenter: parent.verticalCenter; wrapMode: Text.Wrap
            text: walletController.trustedDraftStatus
            color: walletController.trustedDraftAvailable ? Design.text : Design.danger
            font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
    Text {
        x: 28; y: 220; text: "TRANSFER ROUTES"; color: Design.textFaint
        font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
    }
    Text {
        visible: root.routes.length === 0; x: 52; y: 334; width: 410
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        text: walletController.trustedDraftAvailable
            ? "No routes in this draft. Add one of the supported Ethereum or Base routes."
            : "Public Wallet functions remain available. This draft cannot enable transfers."
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 14
    }
    ListView {
        id: routeList; objectName: "trustedRouteList"
        x: 28; y: 246; width: 458; height: 354; clip: true; spacing: 10
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
        objectName: "trustedAddRouteButton"; x: 28; y: 620; width: 218; height: 52
        label: "Add Route"; primary: false
        controlEnabled: walletController.trustedDraftAvailable && root.routes.length < 4
        onTriggered: walletController.beginTrustedRoute()
    }
    FormButton {
        objectName: "trustedReviewButton"; x: 266; y: 620; width: 220; height: 52
        label: "Review & Save"
        controlEnabled: walletController.trustedDraftAvailable
        onTriggered: walletController.showTrustedDraftReview()
    }
    Text {
        objectName: "trustedDraftError"; x: 48; y: 694; width: 418
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        text: walletController.errorMessage; color: Design.danger
        font.family: Design.fontFamily; font.pixelSize: 12
    }
    Text {
        x: 48; y: 754; width: 418; horizontalAlignment: Text.AlignHCenter
        text: "Labels and USD estimates never authorize a transfer"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
