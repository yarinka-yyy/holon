import QtQuick
import "."

PageState {
    id: root
    property var records: walletController.approvalRecords

    ScreenHeader {
        objectName: "approvalsHeader"; x: 28; y: 54; width: 458
        title: "Token Approvals"; subtitle: "Active Account · USDC only"
        onBackRequested: walletController.closeApprovals()
    }

    SurfaceCard {
        x: 28; y: 142; width: 458; height: 82
        Image {
            x: 16; anchors.verticalCenter: parent.verticalCenter
            width: 34; height: 34; source: "assets/user.svg"
            sourceSize: Qt.size(68, 68)
        }
        Text {
            x: 64; y: 15; text: walletController.activeProfile.label || "Account"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 15; font.weight: Font.Medium
        }
        Text {
            x: 64; y: 43; width: 370; elide: Text.ElideMiddle
            text: walletController.activeProfile.address || ""
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }

    Item {
        objectName: "approvalRefreshButton"; x: 350; y: 238; width: 136; height: 36
        function trigger() { walletController.refreshApprovals() }
        Text {
            anchors.right: refreshIcon.left; anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            text: walletController.approvalRefreshing ? "Refreshing…" : "Refresh"
            color: Design.accent; font.family: Design.fontFamily; font.pixelSize: 13
        }
        Image {
            id: refreshIcon; anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: 20; height: 20; source: "assets/refresh.svg"
            sourceSize: Qt.size(40, 40)
            RotationAnimation on rotation {
                running: walletController.approvalRefreshing
                from: 0; to: 360; duration: 800; loops: Animation.Infinite
            }
        }
        MouseArea {
            anchors.fill: parent; enabled: !walletController.approvalRefreshing
            cursorShape: Qt.PointingHandCursor; onClicked: parent.trigger()
        }
    }
    Text {
        x: 28; y: 244; text: "Configured allowances"
        color: Design.textMuted; font.family: Design.fontFamily
        font.pixelSize: 14; font.weight: Font.Medium
    }

    ListView {
        id: approvalList; objectName: "approvalList"
        x: 28; y: 286; width: 458; height: 404
        spacing: 12; clip: true; model: root.records
        boundsBehavior: Flickable.StopAtBounds
        delegate: SurfaceCard {
            required property var modelData
            objectName: "approvalCard_" + modelData.networkId
            width: approvalList.width; height: 194
            property url networkIcon: modelData.networkId === "ethereum"
                ? "assets/ethereum.svg" : "assets/base.png"
            Image {
                x: 16; y: 16; width: 36; height: 36
                source: parent.networkIcon; sourceSize: Qt.size(72, 72)
            }
            Image {
                x: 44; y: 34; width: 22; height: 22
                source: "assets/usdc.png"; sourceSize: Qt.size(44, 44)
            }
            Text {
                x: 78; y: 14; text: modelData.network + " · USDC"
                color: Design.text; font.family: Design.fontFamily
                font.pixelSize: 16; font.weight: Font.DemiBold
            }
            Text {
                x: 78; y: 40; text: "Chain " + modelData.chainId
                color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
            }
            Text {
                anchors.right: parent.right; anchors.rightMargin: 16; y: 17
                text: modelData.statusLabel
                color: modelData.status === "LIVE" ? Design.accent
                    : modelData.status === "UNAVAILABLE" ? Design.danger : Design.warning
                font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold
            }
            Rectangle { x: 16; y: 70; width: 426; height: 1; color: "#0FFFFFFF" }
            Text {
                x: 16; y: 82; text: "Allowance"
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
            }
            Text {
                anchors.right: parent.right; anchors.rightMargin: 16; y: 79
                width: 310; horizontalAlignment: Text.AlignRight; elide: Text.ElideRight
                text: modelData.allowance
                color: Design.text; font.family: Design.fontFamily
                font.pixelSize: 15; font.weight: Font.Medium
            }
            Text {
                x: 16; y: 112; text: "Spender"
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
            }
            Text {
                anchors.right: parent.right; anchors.rightMargin: 16; y: 109
                width: 310; horizontalAlignment: Text.AlignRight; elide: Text.ElideMiddle
                text: modelData.shortSpender || "Local policy not configured"
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
            }
            FormButton {
                objectName: "revokeButton_" + modelData.networkId
                x: 16; y: 140; width: 426; height: 40
                label: walletController.approvalPreparing ? "Preparing…"
                    : modelData.status === "NO_ACTIVE_ALLOWANCE" ? "No allowance to revoke"
                    : "Revoke allowance"
                controlEnabled: modelData.revokeAvailable
                    && !walletController.approvalPreparing
                    && !walletController.approvalRefreshing
                onTriggered: walletController.prepareRevoke(modelData.networkId)
            }
        }
    }

    Text {
        objectName: "approvalErrorLabel"
        x: 48; y: 710; width: 418; horizontalAlignment: Text.AlignHCenter
        text: walletController.approvalError
        color: Design.danger; font.family: Design.fontFamily
        font.pixelSize: 11; wrapMode: Text.Wrap
    }
    Text {
        x: 48; y: 754; width: 418; horizontalAlignment: Text.AlignHCenter
        text: "Read-only inspection does not unlock signing authority"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
    }
}
