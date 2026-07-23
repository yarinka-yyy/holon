import QtQuick
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
        objectName: "revokeReviewScroll"
        width: 458; height: 592; clip: true; contentWidth: width
        contentHeight: root.detailsOpen ? 1070 : 704
        boundsBehavior: Flickable.StopAtBounds

        Rectangle {
            x: 0; y: 0; width: 458; height: 46; radius: 12
            color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
            Text {
                anchors.centerIn: parent; text: "MAINNET REVOKE · NETWORK FEE"
                color: Design.warning; font.family: Design.fontFamily
                font.pixelSize: 11; font.weight: Font.DemiBold
            }
        }
        SurfaceCard {
            x: 0; y: 58; width: 458; height: 102
            Text {
                x: 16; y: 14; text: "Owner · " + (root.action.accountLabel || "Account")
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
            }
            Text {
                x: 16; y: 42; width: 426; wrapMode: Text.WrapAnywhere
                text: root.action.owner || ""; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 12
            }
        }
        SurfaceCard {
            x: 0; y: 170; width: 458; height: 102
            Text {
                x: 16; y: 14; text: "Spender"
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
            }
            Text {
                x: 16; y: 42; width: 426; wrapMode: Text.WrapAnywhere
                text: root.action.spender || ""; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 12
            }
        }
        SummaryRow {
            x: 0; y: 282; width: 458; label: "Current allowance"
            value: root.action.allowanceBefore || "Unavailable"
            secondary: "Will become 0 USDC"; iconSource: "assets/usdc.png"
        }
        SummaryRow {
            x: 0; y: 358; width: 458; label: "Network"
            value: (root.action.network || "") + " · " + (root.action.chainId || "")
            iconSource: "assets/network-data.svg"; badgeSource: root.networkIcon
        }
        SummaryRow {
            x: 0; y: 434; width: 458; label: "Maximum fee"
            value: root.action.maxFeeDisplay || "Unavailable"
            secondary: "Paid in ETH"; iconSource: "assets/info.svg"
        }
        Item {
            objectName: "revokeDetailsButton"; x: 0; y: 518; width: 458; height: 48
            function trigger() { root.detailsOpen = !root.detailsOpen }
            SurfaceCard { anchors.fill: parent; interactive: true; onTriggered: parent.trigger() }
            Text {
                x: 16; anchors.verticalCenter: parent.verticalCenter
                text: "Technical details"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
            }
            Text {
                anchors.right: parent.right; anchors.rightMargin: 18
                anchors.verticalCenter: parent.verticalCenter
                text: root.detailsOpen ? "−" : "+"; color: Design.accent
                font.family: Design.fontFamily; font.pixelSize: 20
            }
        }
        SurfaceCard {
            visible: root.detailsOpen; x: 0; y: 578; width: 458; height: 314
            Column {
                x: 16; y: 10; width: parent.width - 32
                Repeater {
                    model: [
                        ["Transaction target", root.action.shortTransactionTarget || ""],
                        ["USDC contract", root.action.shortContract || ""],
                        ["New allowance", "0 USDC"],
                        ["Data hash", root.action.calldataHash || ""],
                        ["Native value", (root.action.nativeValueWei || "0") + " wei"],
                        ["Nonce", root.action.nonce || ""],
                        ["Gas limit", root.action.gas || ""],
                        ["Observed block", root.action.block || ""],
                        ["Max fee / gas", (root.action.maxFeePerGas || "") + " wei"],
                        ["Priority fee", (root.action.maxPriorityFeePerGas || "") + " wei"],
                        ["Exact maximum fee", (root.action.maxTotalFeeWei || "") + " wei"],
                        ["Local revoke cap", walletController.revokeFeeLimit],
                        ["Action ID", root.action.shortActionId || ""],
                        ["Digest", root.action.shortDigest || ""]
                    ]
                    delegate: Item {
                        required property var modelData
                        width: parent.width; height: 21
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData[0]; color: Design.textFaint
                            font.family: Design.fontFamily; font.pixelSize: 10
                        }
                        Text {
                            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            width: 260; horizontalAlignment: Text.AlignRight; elide: Text.ElideMiddle
                            text: modelData[1]; color: Design.textMuted
                            font.family: Design.fontFamily; font.pixelSize: 10
                        }
                    }
                }
            }
        }
        FormButton {
            objectName: "continueRevokeButton"; x: 0
            y: root.detailsOpen ? 908 : 582; width: 458; height: 56
            label: "Continue"; controlEnabled: walletController.revokeExecutionAvailable
            onTriggered: walletController.beginRevokeExecution()
        }
        Text {
            x: 18; y: root.detailsOpen ? 976 : 650; width: 422
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: walletController.revokeGateMessage
            color: walletController.revokeExecutionAvailable ? Design.textFaint : Design.danger
            font.family: Design.fontFamily; font.pixelSize: 10
        }
    }
}
