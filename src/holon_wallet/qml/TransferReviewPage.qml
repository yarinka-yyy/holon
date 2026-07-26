import QtQuick
import QtQuick.Controls
import "."

TransactionFlowShell {
    id: root
    title: "Confirm Transaction"
    subtitle: action.actionType === "lending"
        ? ((action.operationStep || "Review") + " · Review the exact Aave action")
        : "Review exact transfer details"
    activeStep: 0; onBackRequested: walletController.editTransfer()
    property bool detailsOpen: false
    property var action: walletController.transferAction
    property bool isLending: action.actionType === "lending"
    property string lendingMethod: action.method === "withdraw" && action.amountMode === "all"
        ? "withdraw all" : (action.method || "action")
    property url assetIcon: action.assetId === "eth"
        ? "assets/ethereum.svg" : "assets/usdc.png"
    property url networkIcon: action.networkId === "ethereum"
        ? "assets/ethereum.svg" : "assets/base.png"

    function aaveTitle() {
        if (action.method === "approve") return "Supply " + action.amount + " to Aave V3 · Step 1 of 2 — Approve"
        if (action.method === "supply") return "Supply " + action.amount + " to Aave V3 · Step 2 of 2 — Supply"
        if (action.method === "withdraw" && action.amountMode === "all")
            return "Withdraw all from Aave V3"
        if (action.method === "withdraw") return "Withdraw from Aave V3"
        return "Aave V3 action"
    }
    function aaveDescription() {
        if (action.method === "approve")
            return "Allow Aave V3 to use up to " + (action.amount || "the reviewed amount")
        if (action.method === "supply")
            return (action.amount || "USDC") + " supplied from " + (action.accountLabel || "this Account")
        if (action.amountMode === "all") return "Return the complete current position"
        return "Return " + (action.amount || "the reviewed amount") + " to this Wallet"
    }
    function semanticAaveAction() {
        if (action.method === "approve") return "Approve Aave V3"
        if (action.method === "supply") return "Supply to Aave V3"
        if (action.method === "withdraw" && action.amountMode === "all")
            return "Withdraw all from Aave V3"
        return "Withdraw from Aave V3"
    }

    Flickable {
        id: reviewScroll; objectName: "transferReviewScroll"
        width: 458; height: 430; clip: true; contentWidth: width
        contentHeight: root.detailsOpen ? 900 : 458
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Rectangle {
            objectName: "mainnetTransferBanner"; x: 0; y: 0; width: 458; height: 54
            radius: 12; clip: true
            color: root.isLending ? Design.surfaceCard : "#332C261B"
            border.width: 1; border.color: root.isLending ? "#448D86FF" : "#66D5AA64"
            Image {
                anchors.fill: parent; visible: root.isLending
                source: "assets/aave-banner.png"; fillMode: Image.PreserveAspectCrop
                sourceSize: Qt.size(916, 108)
            }
            Text {
                anchors.centerIn: parent; visible: !root.isLending
                text: "MAINNET TRANSFER · REAL FUNDS"
                color: Design.warning; font.family: Design.fontFamily
                font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 0.3
            }
        }

        SurfaceCard {
            objectName: "transferReviewAccount"; x: 0; y: 64; width: 458; height: 66
            Image { x: 16; y: 14; width: 22; height: 22; source: "assets/user.svg" }
            Text {
                x: 50; y: 11; text: "From · " + (root.action.accountLabel || "Account")
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
            }
            Text {
                x: 50; y: 35; width: 390; text: root.action.sender || ""
                color: Design.text; font.family: Design.fontFamily; font.pixelSize: 11
                elide: Text.ElideMiddle
            }
        }
        SurfaceCard {
            objectName: "transferReviewRecipient"; x: 0; y: 138; width: 458; height: 82
            Image {
                x: 16; y: 14; width: 24; height: 24
                source: root.isLending ? "assets/usdc.png" : "assets/user.svg"
            }
            Text {
                x: 52; y: 11; width: 386
                text: root.isLending ? root.aaveTitle() : "To"
                color: Design.text; font.family: Design.fontFamily
                font.pixelSize: 14; font.weight: Font.Medium
            }
            Text {
                x: 52; y: 35; width: 386
                text: root.isLending ? root.aaveDescription() : (root.action.recipient || "")
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
                elide: root.isLending ? Text.ElideRight : Text.ElideMiddle
            }
            Text {
                visible: root.isLending; x: 52; y: 57; width: 386
                text: "Receiver · " + (root.action.accountLabel || "Active Wallet")
                color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
            }
        }
        SummaryRow {
            objectName: "transferReviewAmount"; x: 0; y: 224; width: 458; compact: true
            label: "Amount"; value: root.action.amount || ""
            secondary: walletController.transferAmountUsd; iconSource: root.assetIcon
        }
        SummaryRow {
            objectName: "transferReviewNetwork"; x: 0; y: 284; width: 458; compact: true
            label: "Network"; value: root.action.network || ""
            iconSource: "assets/network-data.svg"; badgeSource: root.networkIcon
        }
        SummaryRow {
            objectName: "transferReviewFee"; x: 0; y: 344; width: 458; compact: true
            label: "Maximum fee"; value: walletController.transferFeeUsd
            iconSource: "assets/info.svg"
        }

        Item {
            objectName: "transferDetailsButton"; x: 0; y: 410; width: 458; height: 48
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
            visible: root.detailsOpen; x: 0; y: 470; width: 458; height: 420
            Column {
                x: 16; y: 10; width: parent.width - 32; spacing: 0
                Repeater {
                    model: [
                        ["Network / chain", (root.action.network || "") + " · " + (root.action.chainId || "")],
                        ["Transaction target", root.action.shortTransactionTarget || ""],
                        ["Receiver", root.isLending
                            ? (root.action.sender || "")
                            : (root.action.recipient || "")],
                        ["Method", root.action.method || "transfer"],
                        ["Action", root.isLending ? root.semanticAaveAction() : "Transfer"],
                        ["Amount mode", root.action.amountMode || "exact"],
                        ["Action profile", root.action.actionProfileDigest || ""],
                        ["Contract", root.action.shortContract || "Native asset"],
                        ["Data hash", root.action.calldataHash || ""],
                        ["Native value", (root.action.nativeValueWei || "0") + " wei"],
                        ["Nonce", root.action.nonce || ""],
                        ["Gas limit", root.action.gas || ""],
                        ["Observed block", root.action.block || ""],
                        ["Max fee / gas", (root.action.maxFeePerGas || "") + " wei"],
                        ["Priority fee", (root.action.maxPriorityFeePerGas || "") + " wei"],
                        ["Exact maximum fee", (root.action.maxTotalFeeWei || "") + " wei"],
                        ["Maximum fee (ETH)", root.action.maxFeeDisplay || "Unavailable"],
                        ["L2 fee ceiling", (root.action.l2FeeCeilingWei || "0") + " wei"],
                        ["L1 fee upper bound", (root.action.l1FeeUpperBoundWei || "0") + " wei"],
                        ["Local fee cap", walletController.mainnetFeeLimit],
                        ["Local amount cap", walletController.mainnetAmountLimit],
                        ["Action ID", root.action.shortActionId || ""],
                        ["Digest", root.action.shortDigest || ""]
                    ]
                    delegate: Item {
                        required property var modelData
                        width: parent.width; height: 18
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData[0]; color: Design.textFaint
                            font.family: Design.fontFamily; font.pixelSize: 9
                        }
                        Text {
                            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            width: 260; horizontalAlignment: Text.AlignRight; elide: Text.ElideMiddle
                            text: modelData[1]; color: Design.textMuted
                            font.family: Design.fontFamily; font.pixelSize: 9
                        }
                    }
                }
            }
        }
    }
    Rectangle {
        objectName: "transferReviewOverflowCue"
        x: 0; y: 402; width: 458; height: 28; z: 3
        visible: reviewScroll.contentHeight > reviewScroll.height
            && reviewScroll.contentY < reviewScroll.contentHeight - reviewScroll.height - 2
        gradient: Gradient {
            GradientStop { position: 0; color: "transparent" }
            GradientStop { position: 1; color: Design.background }
        }
    }
    Text {
        x: 12; y: 434; width: 434; height: 26
        visible: !walletController.mainnetExecutionAvailable
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        text: walletController.mainnetGateMessage
        color: Design.danger; font.family: Design.fontFamily; font.pixelSize: 10
    }
    FormButton {
        objectName: "continueMainnetButton"; x: 0; y: 466; width: 458; height: 54
        label: "Continue"; controlEnabled: walletController.mainnetExecutionAvailable
        onTriggered: walletController.beginMainnetExecution()
    }
    FormButton {
        objectName: "editTransferButton"; x: 0; y: 532; width: 458; height: 48
        label: root.isLending ? "Change action" : "Edit transfer"; primary: false
        onTriggered: walletController.editTransfer()
    }
}
