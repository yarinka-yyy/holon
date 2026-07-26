import QtQuick
import QtQuick.Controls
import "."

TransactionFlowShell {
    id: root
    title: "Revoke Approval"; subtitle: "Review the exact USDC permission"
    activeStep: 0; onBackRequested: walletController.editRevoke()
    property bool detailsOpen: false
    property var action: walletController.revokeAction
    property url networkIcon: action.networkId === "ethereum"
        ? "assets/ethereum.svg" : "assets/base.png"

    Flickable {
        id: revokeScroll; objectName: "revokeReviewScroll"
        width: 458; height: 494; clip: true; contentWidth: width
        contentHeight: root.detailsOpen ? 786 : 410
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Rectangle {
            x: 0; y: 0; width: 458; height: 44; radius: 12
            color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
            Text {
                anchors.centerIn: parent; text: "MAINNET REVOKE · NETWORK FEE"
                color: Design.warning; font.family: Design.fontFamily
                font.pixelSize: 11; font.weight: Font.DemiBold
            }
        }
        SurfaceCard {
            x: 0; y: 54; width: 458; height: 66
            Text { x: 16; y: 11; text: "Owner · " + (root.action.accountLabel || "Account"); color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12 }
            Text { x: 16; y: 35; width: 426; elide: Text.ElideMiddle; text: root.action.owner || ""; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 11 }
        }
        SurfaceCard {
            x: 0; y: 128; width: 458; height: 66
            Text { x: 16; y: 11; text: "Spender"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12 }
            Text { x: 16; y: 35; width: 426; elide: Text.ElideMiddle; text: root.action.spender || ""; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 11 }
        }
        SummaryRow {
            x: 0; y: 198; width: 458; compact: true; label: "Current allowance"
            value: root.action.allowanceBefore || "Unavailable"
            secondary: "Will become 0 USDC"; iconSource: "assets/usdc.png"
        }
        SummaryRow {
            x: 0; y: 258; width: 458; compact: true; label: "Network"
            value: root.action.network || ""
            iconSource: "assets/network-data.svg"; badgeSource: root.networkIcon
        }
        SummaryRow {
            x: 0; y: 318; width: 458; compact: true; label: "Maximum fee"
            value: walletController.revokeFeeUsd; iconSource: "assets/info.svg"
        }
        Item {
            objectName: "revokeDetailsButton"; x: 0; y: 382; width: 458; height: 48
            function trigger() { root.detailsOpen = !root.detailsOpen }
            SurfaceCard { anchors.fill: parent; interactive: true; onTriggered: parent.trigger() }
            Text { x: 16; anchors.verticalCenter: parent.verticalCenter; text: "Technical details"; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium }
            Text { anchors.right: parent.right; anchors.rightMargin: 18; anchors.verticalCenter: parent.verticalCenter; text: root.detailsOpen ? "−" : "+"; color: Design.accent; font.family: Design.fontFamily; font.pixelSize: 20 }
        }
        SurfaceCard {
            visible: root.detailsOpen; x: 0; y: 442; width: 458; height: 314
            Column {
                x: 16; y: 10; width: parent.width - 32
                Repeater {
                    model: [
                        ["Network / chain", (root.action.network || "") + " · " + (root.action.chainId || "")],
                        ["Transaction target", root.action.shortTransactionTarget || ""],
                        ["USDC contract", root.action.shortContract || ""],
                        ["New allowance", "0 USDC"],
                        ["Data hash", root.action.calldataHash || ""],
                        ["Native value", (root.action.nativeValueWei || "0") + " wei"],
                        ["Nonce", root.action.nonce || ""], ["Gas limit", root.action.gas || ""],
                        ["Observed block", root.action.block || ""],
                        ["Max fee / gas", (root.action.maxFeePerGas || "") + " wei"],
                        ["Priority fee", (root.action.maxPriorityFeePerGas || "") + " wei"],
                        ["Exact maximum fee", (root.action.maxTotalFeeWei || "") + " wei"],
                        ["Maximum fee (ETH)", root.action.maxFeeDisplay || "Unavailable"],
                        ["Local revoke cap", walletController.revokeFeeLimit],
                        ["Action ID", root.action.shortActionId || ""], ["Digest", root.action.shortDigest || ""]
                    ]
                    delegate: Item {
                        required property var modelData
                        width: parent.width; height: 18
                        Text { anchors.verticalCenter: parent.verticalCenter; text: modelData[0]; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9 }
                        Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: 260; horizontalAlignment: Text.AlignRight; elide: Text.ElideMiddle; text: modelData[1]; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 9 }
                    }
                }
            }
        }
    }
    Rectangle {
        objectName: "approvalReviewOverflowCue"
        x: 0; y: 466; width: 458; height: 28; z: 3
        visible: revokeScroll.contentHeight > revokeScroll.height
            && revokeScroll.contentY < revokeScroll.contentHeight - revokeScroll.height - 2
        gradient: Gradient { GradientStop { position: 0; color: "transparent" } GradientStop { position: 1; color: Design.background } }
    }
    Text {
        x: 12; y: 498; width: 434; height: 24
        visible: !walletController.revokeExecutionAvailable
        horizontalAlignment: Text.AlignHCenter; text: walletController.revokeGateMessage
        color: Design.danger; font.family: Design.fontFamily; font.pixelSize: 10
    }
    FormButton {
        objectName: "continueRevokeButton"; x: 0; y: 528; width: 458; height: 56
        label: "Continue"; controlEnabled: walletController.revokeExecutionAvailable
        onTriggered: walletController.beginRevokeExecution()
    }
}
