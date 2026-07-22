import QtQuick
import "."

TransactionFlowShell {
    id: root
    title: "Confirm Transaction"; subtitle: "Review exact transfer details"
    activeStep: 0; onBackRequested: walletController.editTransfer()
    property bool detailsOpen: false
    property var action: walletController.transferAction

    Flickable {
        id: reviewScroll; objectName: "transferReviewScroll"
        width: 458; height: 592; clip: true; contentWidth: width
        contentHeight: root.detailsOpen ? 980 : 690
        boundsBehavior: Flickable.StopAtBounds
        Rectangle {
            objectName: "mainnetTransferBanner"; x: 0; y: 0; width: 458; height: 46
            radius: 12; color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
            Text {
                anchors.centerIn: parent; text: "MAINNET TRANSFER · REAL FUNDS"
                color: Design.warning; font.family: Design.fontFamily
                font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 0.3
            }
        }
        Column {
            x: 0; y: 58; width: 458; spacing: 0
            SummaryRow {
                objectName: "transferReviewAccount"; width: parent.width
                label: "From"; value: root.action.accountLabel || "Account"
                secondary: root.action.shortSender || ""; iconSource: "assets/user.svg"
            }
            SummaryRow {
                objectName: "transferReviewRecipient"; width: parent.width
                label: "To"; value: root.action.shortRecipient || ""
                iconSource: "assets/user.svg"
            }
            SummaryRow {
                objectName: "transferReviewAmount"; width: parent.width
                label: "Amount"; value: root.action.amount || "1 USDC"
                secondary: walletController.transferAmountUsd
                iconSource: "assets/usdc.png"
            }
            SummaryRow {
                objectName: "transferReviewNetwork"; width: parent.width
                label: "Network"; value: (root.action.network || "Base")
                    + " · " + (root.action.chainId || "8453")
                secondary: ""
                iconSource: "assets/network-data.svg"; badgeSource: "assets/base.png"
            }
            SummaryRow {
                objectName: "transferReviewFee"; width: parent.width
                label: "Estimated fee"; value: walletController.transferFeeUsd
                secondary: root.action.maxFeeDisplay || "Unavailable"
                iconSource: "assets/info.svg"
            }
        }
        Item {
            objectName: "transferDetailsButton"; x: 0; y: 442; width: 458; height: 48
            function trigger() { root.detailsOpen = !root.detailsOpen }
            SurfaceCard {
                anchors.fill: parent; interactive: true; onTriggered: parent.trigger()
            }
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
            visible: root.detailsOpen; x: 0; y: 504; width: 458; height: 278
            Column {
                x: 16; y: 12; width: parent.width - 32; spacing: 0
                Repeater {
                    model: [
                        ["Contract", root.action.shortContract || ""],
                        ["Calldata hash", root.action.calldataHash || ""],
                        ["Nonce", root.action.nonce || ""],
                        ["Gas limit", root.action.gas || ""],
                        ["Observed block", root.action.block || ""],
                        ["Max fee / gas", (root.action.maxFeePerGas || "") + " wei"],
                        ["Priority fee", (root.action.maxPriorityFeePerGas || "") + " wei"],
                        ["Exact maximum", (root.action.maxTotalFeeWei || "") + " wei"],
                        ["Local fee cap", walletController.mainnetFeeLimit],
                        ["Action ID", root.action.shortActionId || ""],
                        ["Digest", root.action.shortDigest || ""]
                    ]
                    delegate: Item {
                        required property var modelData
                        width: parent.width; height: 23
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData[0]; color: Design.textFaint
                            font.family: Design.fontFamily; font.pixelSize: 10
                        }
                        Text {
                            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            width: 270; horizontalAlignment: Text.AlignRight; elide: Text.ElideMiddle
                            text: modelData[1]; color: Design.textMuted
                            font.family: Design.fontFamily; font.pixelSize: 10
                        }
                    }
                }
            }
        }
        FormButton {
            objectName: "continueMainnetButton"; x: 0
            y: root.detailsOpen ? 798 : 510; width: 458; height: 56
            label: "Continue"; controlEnabled: walletController.mainnetExecutionAvailable
            onTriggered: walletController.beginMainnetExecution()
        }
        Text {
            x: 18; y: root.detailsOpen ? 864 : 576; width: 422
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: walletController.mainnetGateMessage
            color: walletController.mainnetExecutionAvailable ? Design.textFaint : Design.danger
            font.family: Design.fontFamily; font.pixelSize: 10
        }
        FormButton {
            objectName: "editTransferButton"; x: 0
            y: root.detailsOpen ? 908 : 620; width: 458; height: 48
            label: "Edit recipient"; primary: false
            onTriggered: walletController.editTransfer()
        }
    }
}
