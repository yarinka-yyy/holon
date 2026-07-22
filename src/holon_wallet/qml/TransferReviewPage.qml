import QtQuick
import "."

TransactionFlowShell {
    id: root
    title: "Confirm Transaction"; subtitle: "Review exact transfer details"
    activeStep: 0; onBackRequested: walletController.editTransfer()
    property bool detailsOpen: false
    property var action: walletController.transferAction
    property url assetIcon: action.assetId === "eth"
        ? "assets/ethereum.svg" : "assets/usdc.png"
    property url networkIcon: action.networkId === "ethereum"
        ? "assets/ethereum.svg" : "assets/base.png"

    Flickable {
        id: reviewScroll; objectName: "transferReviewScroll"
        width: 458; height: 592; clip: true; contentWidth: width
        contentHeight: root.detailsOpen ? 1090 : 730
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

        SurfaceCard {
            objectName: "transferReviewAccount"; x: 0; y: 58; width: 458; height: 92
            Image {
                x: 16; y: 18; width: 24; height: 24
                source: "assets/user.svg"; sourceSize: Qt.size(48, 48)
            }
            Text {
                x: 52; y: 14; text: "From · " + (root.action.accountLabel || "Account")
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
            }
            Text {
                x: 52; y: 40; width: 382; text: root.action.sender || ""
                color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
                wrapMode: Text.WrapAnywhere
            }
        }
        SurfaceCard {
            objectName: "transferReviewRecipient"; x: 0; y: 158; width: 458; height: 92
            Image {
                x: 16; y: 18; width: 24; height: 24
                source: "assets/user.svg"; sourceSize: Qt.size(48, 48)
            }
            Text {
                x: 52; y: 14; text: "To"; color: Design.textMuted
                font.family: Design.fontFamily; font.pixelSize: 13
            }
            Text {
                x: 52; y: 40; width: 382; text: root.action.recipient || ""
                color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
                wrapMode: Text.WrapAnywhere
            }
        }
        SummaryRow {
            objectName: "transferReviewAmount"; x: 0; y: 258; width: 458
            label: "Amount"; value: root.action.amount || ""
            secondary: walletController.transferAmountUsd; iconSource: root.assetIcon
        }
        SummaryRow {
            objectName: "transferReviewNetwork"; x: 0; y: 334; width: 458
            label: "Network"; value: (root.action.network || "")
                + " · " + (root.action.chainId || "")
            iconSource: "assets/network-data.svg"; badgeSource: root.networkIcon
        }
        SummaryRow {
            objectName: "transferReviewFee"; x: 0; y: 410; width: 458
            label: "Maximum fee"; value: walletController.transferFeeUsd
            secondary: root.action.maxFeeDisplay || "Unavailable"
            iconSource: "assets/info.svg"
        }

        Item {
            objectName: "transferDetailsButton"; x: 0; y: 494; width: 458; height: 48
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
            visible: root.detailsOpen; x: 0; y: 554; width: 458; height: 330
            Column {
                x: 16; y: 10; width: parent.width - 32; spacing: 0
                Repeater {
                    model: [
                        ["Transaction target", root.action.shortTransactionTarget || ""],
                        ["Contract", root.action.shortContract || "Native asset"],
                        ["Data hash", root.action.calldataHash || ""],
                        ["Native value", (root.action.nativeValueWei || "0") + " wei"],
                        ["Nonce", root.action.nonce || ""],
                        ["Gas limit", root.action.gas || ""],
                        ["Observed block", root.action.block || ""],
                        ["Max fee / gas", (root.action.maxFeePerGas || "") + " wei"],
                        ["Priority fee", (root.action.maxPriorityFeePerGas || "") + " wei"],
                        ["Exact maximum fee", (root.action.maxTotalFeeWei || "") + " wei"],
                        ["Local fee cap", walletController.mainnetFeeLimit],
                        ["Local amount cap", walletController.mainnetAmountLimit],
                        ["Action ID", root.action.shortActionId || ""],
                        ["Digest", root.action.shortDigest || ""]
                    ]
                    delegate: Item {
                        required property var modelData
                        width: parent.width; height: 22
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
            objectName: "continueMainnetButton"; x: 0
            y: root.detailsOpen ? 900 : 558; width: 458; height: 56
            label: "Continue"; controlEnabled: walletController.mainnetExecutionAvailable
            onTriggered: walletController.beginMainnetExecution()
        }
        Text {
            x: 18; y: root.detailsOpen ? 968 : 626; width: 422
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: walletController.mainnetGateMessage
            color: walletController.mainnetExecutionAvailable ? Design.textFaint : Design.danger
            font.family: Design.fontFamily; font.pixelSize: 10
        }
        FormButton {
            objectName: "editTransferButton"; x: 0
            y: root.detailsOpen ? 1024 : 678; width: 458; height: 48
            label: "Edit transfer"; primary: false
            onTriggered: walletController.editTransfer()
        }
    }
}
