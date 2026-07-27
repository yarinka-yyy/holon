import QtQuick
import QtQuick.Controls
import "."

TransactionFlowShell {
    id: root
    title: "Confirm Transaction"
    subtitle: action.actionType === "lending"
        ? ((action.operationStep || "Review") + " · Review the exact Lending action")
        : "Review exact transfer details"
    activeStep: 0; onBackRequested: walletController.editTransfer()
    property bool detailsOpen: false
    property var action: walletController.transferAction
    property bool isLending: action.actionType === "lending"
    property bool highLendingFee: root.isLending && walletController.lendingHighFeeWarning
    property string lendingMethod: action.method === "withdraw" && action.amountMode === "all"
        ? "withdraw all" : (action.method || "action")
    property url assetIcon: action.assetId === "eth"
        ? "assets/ethereum.svg" : "assets/usdc.png"
    property url networkIcon: action.networkId === "ethereum"
        ? "assets/ethereum.svg" : "assets/base.png"
    readonly property int reviewCardWidth: 458
    readonly property int accountY: root.isLending ? 0 : 50
    readonly property int recipientY: root.isLending ? 74 : 118
    readonly property int summaryY: root.isLending ? 154 : 190
    readonly property int detailsY: root.isLending
        ? (root.highLendingFee ? 384 : 340) : 372
    readonly property int collapsedContentHeight: root.detailsY + 48

    function lendingTitle() {
        if (action.method === "approve" || action.method === "supply" || action.method === "deposit")
            return "Supply " + action.amount + " to " + action.protocolLabel
        if (action.amountMode === "all") return "Withdraw all from " + action.protocolLabel
        if (action.method === "withdraw") return "Withdraw from " + action.protocolLabel
        return "Lending action"
    }
    function lendingStep() {
        if (action.method === "approve") return "Step 1 of 2 · Approve"
        if (action.method === "supply" || action.method === "deposit") return "Step 2 of 2 · Supply"
        if (action.amountMode === "all") return "All available funds"
        return "Exact amount"
    }
    function semanticLendingAction() {
        if (action.method === "approve") return "Approve " + action.protocolLabel
        if (action.method === "supply" || action.method === "deposit") return "Supply to " + action.protocolLabel
        if (action.amountMode === "all") return "Withdraw all from " + action.protocolLabel
        return "Withdraw from " + action.protocolLabel
    }

    Flickable {
        id: reviewScroll; objectName: "transferReviewScroll"
        width: 458; height: 430; clip: true; contentWidth: width
        contentHeight: root.detailsOpen ? root.detailsY + 480 : root.collapsedContentHeight
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { width: 6; policy: ScrollBar.AsNeeded }

        Rectangle {
            objectName: "mainnetTransferBanner"; visible: !root.isLending
            x: 0; y: 0; width: root.reviewCardWidth; height: 40
            radius: 12; clip: true
            color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
            Text {
                anchors.centerIn: parent
                text: "MAINNET TRANSFER · REAL FUNDS"
                color: Design.warning; font.family: Design.fontFamily
                font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 0.3
            }
        }

        SurfaceCard {
            objectName: "transferReviewAccount"; x: 0; y: root.accountY
            width: root.reviewCardWidth; height: 66
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
            objectName: "transferReviewRecipient"; x: 0; y: root.recipientY
            width: root.reviewCardWidth; height: root.isLending ? 72 : 66
            Image {
                x: 16; y: 14; width: 24; height: 24
                source: root.isLending ? "assets/usdc.png" : "assets/user.svg"
            }
            Image {
                visible: root.isLending
                x: parent.width - 126; y: 24; width: 110; height: 22
                source: root.action.protocolLogo || ""
                fillMode: Image.PreserveAspectFit
                sourceSize: Qt.size(220, 44)
            }
            Text {
                x: 52; y: 11; width: root.isLending ? 248 : 370
                text: root.isLending ? root.lendingTitle() : "To"
                color: Design.text; font.family: Design.fontFamily
                font.pixelSize: 14; font.weight: Font.Medium
            }
            Text {
                x: 52; y: 35; width: root.isLending ? 248 : 370
                text: root.isLending ? root.lendingStep() : (root.action.recipient || "")
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
                elide: root.isLending ? Text.ElideRight : Text.ElideMiddle
            }
        }
        SummaryRow {
            objectName: "transferReviewAmount"; x: 0; y: root.summaryY
            width: root.reviewCardWidth; compact: true
            label: "Amount"; value: root.action.amount || ""
            secondary: walletController.transferAmountUsd; iconSource: root.assetIcon
        }
        SummaryRow {
            objectName: "transferReviewNetwork"; x: 0; y: root.summaryY + 60
            width: root.reviewCardWidth; compact: true
            label: "Network"; value: root.action.network || ""
            iconSource: "assets/network-data.svg"; badgeSource: root.networkIcon
        }
        SummaryRow {
            objectName: "transferReviewFee"; x: 0; y: root.summaryY + 120
            width: root.reviewCardWidth; compact: true
            label: "Maximum fee"; value: walletController.transferFeeUsd
            iconSource: "assets/info.svg"
        }
        Rectangle {
            objectName: "lendingHighFeeWarning"
            visible: root.highLendingFee
            x: 0; y: root.summaryY + 174; width: root.reviewCardWidth; height: 38
            radius: 10; color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
            Text {
                anchors.centerIn: parent; width: parent.width - 24
                text: "Base network fee is unusually high. You can still continue."
                horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
                color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 10
            }
        }

        Item {
            objectName: "transferDetailsButton"; x: 0; y: root.detailsY
            width: root.reviewCardWidth; height: 48
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
            visible: root.detailsOpen; x: 0; y: root.detailsY + 60
            width: root.reviewCardWidth; height: 420
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
                        ["Protocol", root.isLending ? (root.action.protocolLabel || "") : ""],
                        ["Action", root.isLending ? root.semanticLendingAction() : "Transfer"],
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
                        ["Local fee rule", walletController.mainnetFeeLimit],
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
        x: 438; y: 400; width: 18; height: 24; z: 3
        color: "transparent"
        visible: reviewScroll.contentHeight > reviewScroll.height
            && reviewScroll.contentY < reviewScroll.contentHeight - reviewScroll.height - 2
        Image {
            anchors.centerIn: parent; width: 16; height: 16
            source: "assets/chevron-down.svg"; opacity: 0.8
            transform: Translate {
                SequentialAnimation on y {
                    running: reviewScroll.contentHeight > reviewScroll.height
                        && reviewScroll.contentY < reviewScroll.contentHeight - reviewScroll.height - 2
                    loops: Animation.Infinite
                    NumberAnimation { to: 3; duration: 420; easing.type: Easing.InOutQuad }
                    NumberAnimation { to: 0; duration: 420; easing.type: Easing.InOutQuad }
                }
            }
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
