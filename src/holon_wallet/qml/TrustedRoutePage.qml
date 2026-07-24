import QtQuick
import "."

PageState {
    id: root
    property var route: walletController.trustedRoute
    property string networkId: "base"
    property string assetId: "usdc"
    function load() {
        networkId = route.networkId || "base"
        assetId = route.assetId || "usdc"
        routeAmount.text = route.routeAmount || ""
        feeAmount.text = route.feeAmount || ""
    }
    onActiveChanged: if (active) load()

    ScreenHeader {
        objectName: "trustedRouteHeader"; x: 28; y: 54; width: 458
        title: route.isNew ? "Add Route" : route.networkLabel + " · " + route.assetLabel
        subtitle: "Exact per-transfer limits"
        onBackRequested: walletController.closeTrustedRoute()
    }
    Text { x: 28; y: 130; text: "NETWORK"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11 }
    Row {
        x: 28; y: 152; spacing: 12
        Repeater {
            model: [{id: "ethereum", label: "Ethereum"}, {id: "base", label: "Base"}]
            delegate: Rectangle {
                required property var modelData
                objectName: "trustedNetwork_" + modelData.id
                width: 223; height: 42; radius: Design.controlRadius
                color: root.networkId === modelData.id ? Design.accentSoft : Design.surface
                border.width: root.networkId === modelData.id ? 2 : 1
                border.color: root.networkId === modelData.id ? Design.accent : Design.border
                Text { anchors.centerIn: parent; text: modelData.label; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 13 }
                MouseArea {
                    anchors.fill: parent; enabled: root.route.isNew
                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: root.networkId = modelData.id
                }
            }
        }
    }
    Text { x: 28; y: 210; text: "ASSET"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11 }
    Row {
        x: 28; y: 232; spacing: 12
        Repeater {
            model: [{id: "eth", label: "ETH"}, {id: "usdc", label: "USDC"}]
            delegate: Rectangle {
                required property var modelData
                objectName: "trustedAsset_" + modelData.id
                width: 223; height: 42; radius: Design.controlRadius
                color: root.assetId === modelData.id ? Design.accentSoft : Design.surface
                border.width: root.assetId === modelData.id ? 2 : 1
                border.color: root.assetId === modelData.id ? Design.accent : Design.border
                Text { anchors.centerIn: parent; text: modelData.label; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 13 }
                MouseArea {
                    anchors.fill: parent; enabled: root.route.isNew
                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: root.assetId = modelData.id
                }
            }
        }
    }
    DraftField {
        id: routeAmount; objectName: "trustedRouteAmountField"; fieldObjectName: "trustedRouteAmountInput"
        x: 28; y: 292; width: 218; height: 70
        label: "Max per transfer · " + root.assetId.toUpperCase(); placeholderText: "100"
    }
    Text {
        x: 28; y: 366; width: 218; text: walletController.trustedAmountUsd(root.assetId, routeAmount.text)
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
    DraftField {
        id: feeAmount; objectName: "trustedFeeAmountField"; fieldObjectName: "trustedFeeAmountInput"
        x: 266; y: 292; width: 220; height: 70
        label: "Max total network fee · ETH"; placeholderText: "0.005"
    }
    Text {
        x: 266; y: 366; width: 220; text: walletController.trustedFeeUsd(feeAmount.text)
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
    FormButton {
        objectName: "trustedSaveRouteButton"; x: 28; y: 398; width: 458; height: 46
        label: route.isNew ? "Create Draft Route" : "Update Route Limits"
        onTriggered: walletController.saveTrustedRoute(root.networkId, root.assetId, routeAmount.text, feeAmount.text)
    }
    Text { x: 28; y: 466; text: "RECIPIENTS"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11 }
    ListView {
        id: recipientList; objectName: "trustedRecipientList"
        x: 28; y: 490; width: 458; height: 142; clip: true; spacing: 8
        model: root.route.recipients || []; boundsBehavior: Flickable.StopAtBounds
        delegate: SurfaceCard {
            required property var modelData
            objectName: "trustedRecipient_" + index
            width: recipientList.width; height: 64
            Text { x: 14; y: 10; text: modelData.label; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 14; font.weight: Font.Medium }
            Text { x: 14; y: 35; text: modelData.shortAddress; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
            Text { anchors.right: parent.right; anchors.rightMargin: 14; y: 12; text: "≤ " + modelData.maxAmount + " " + root.route.assetLabel; color: Design.accent; font.family: Design.fontFamily; font.pixelSize: 12 }
            Text { anchors.right: parent.right; anchors.rightMargin: 14; y: 36; text: modelData.maxAmountUsd; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10 }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: walletController.beginTrustedRecipient(modelData.address) }
        }
    }
    FormButton {
        objectName: "trustedAddRecipientButton"; x: 28; y: 650; width: 218; height: 46
        label: "Add Recipient"; primary: false; controlEnabled: !root.route.isNew
        onTriggered: walletController.beginTrustedRecipient("")
    }
    FormButton {
        objectName: "trustedDeleteRouteButton"; x: 266; y: 650; width: 220; height: 46
        label: "Delete Route"; primary: false; controlEnabled: !root.route.isNew
        onTriggered: walletController.deleteTrustedRoute()
    }
    Text {
        objectName: "trustedRouteError"; x: 48; y: 714; width: 418
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        text: walletController.errorMessage; color: Design.danger
        font.family: Design.fontFamily; font.pixelSize: 11
    }
}
