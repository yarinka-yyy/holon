import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    property bool detailsOpen: false
    property var action: walletController.transferAction

    BackButton {
        objectName: "transferReviewBackButton"; x: 22; y: 42
        onTriggered: walletController.editTransfer()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 49
        text: "Review Transfer"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Rectangle {
        objectName: "unsignedTransferBanner"
        x: 24; y: 98; width: 466; height: 38; radius: 10
        color: action.simulation ? "#28194F" : "#182849"
        border.width: 1; border.color: action.simulation ? Design.purple : Design.blue
        Text {
            anchors.centerIn: parent
            text: action.simulation
                ? "SIMULATED TEST DATA  ·  OFFLINE SIGNING ONLY"
                : "OFFLINE SIGNING  ·  NOTHING WILL BE SENT"
            color: action.simulation ? Design.purpleBright : "#87A6FF"
            font.family: Design.fontFamily
            font.pixelSize: 9; font.weight: Font.Bold; font.letterSpacing: 0.35
        }
    }

    Flickable {
        id: reviewScroll
        objectName: "transferReviewScroll"
        x: 18; y: 150; width: 478; height: 512
        clip: true; contentWidth: width
        contentHeight: root.detailsOpen ? 760 : 500

        Rectangle {
            x: 0; y: 0; width: 478; height: 280; radius: 15
            color: Design.surface; border.width: 1; border.color: Design.border
            GlowWave { x: 230; y: 185; width: 248; height: 67; opacity: 0.27 }

            Text {
                objectName: "transferReviewAccount"
                x: 20; y: 17; text: action.accountLabel || "Account"
                color: Design.text; font.family: Design.fontFamily
                font.pixelSize: 18; font.weight: Font.DemiBold
            }
            Text {
                objectName: "transferReviewSender"
                x: 20; y: 45; text: action.shortSender || ""
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10
            }
            Rectangle { x: 18; y: 70; width: 442; height: 1; color: Design.border }

            Text { x: 20; y: 88; text: "Recipient"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10 }
            Text { objectName: "transferReviewRecipient"; anchors.right: parent.right; anchors.rightMargin: 20; y: 86; text: action.shortRecipient || ""; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 11 }
            Text { x: 20; y: 122; text: "Network"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10 }
            Text { objectName: "transferReviewNetwork"; anchors.right: parent.right; anchors.rightMargin: 20; y: 120; text: (action.network || "") + "  ·  " + (action.chainId || ""); color: Design.text; font.family: Design.fontFamily; font.pixelSize: 11 }
            Text { x: 20; y: 156; text: "Amount"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10 }
            Text { objectName: "transferReviewAmount"; anchors.right: parent.right; anchors.rightMargin: 20; y: 151; text: action.amount || ""; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold }
            Text { x: 20; y: 194; text: "Maximum network fee"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10 }
            Text { objectName: "transferReviewFee"; anchors.right: parent.right; anchors.rightMargin: 20; y: 192; text: action.maxFeeDisplay || ""; color: Design.purpleBright; font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold }
            Text { x: 20; y: 224; text: "Local signing limit"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10 }
            Text { objectName: "offlineSigningLimit"; anchors.right: parent.right; anchors.rightMargin: 20; y: 222; text: walletController.offlineSigningLimit; color: walletController.offlineSigningAvailable ? "#76E1C0" : "#FF91AF"; font.family: Design.fontFamily; font.pixelSize: 10 }
            Text { x: 20; y: 253; text: "Expires"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10 }
            Text { objectName: "transferReviewExpiry"; anchors.right: parent.right; anchors.rightMargin: 20; y: 251; text: action.expiresAt || ""; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 10 }
        }

        Item {
            objectName: "transferDetailsButton"
            x: 0; y: 296; width: 478; height: 42
            function trigger() { root.detailsOpen = !root.detailsOpen }
            Rectangle {
                anchors.fill: parent; radius: 10
                color: detailsMouse.containsMouse ? Design.surfaceHover : Design.surface
                border.width: 1; border.color: root.detailsOpen ? Design.purple : Design.border
            }
            Text {
                x: 15; anchors.verticalCenter: parent.verticalCenter
                text: "Technical details"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
            }
            Text {
                anchors.right: parent.right; anchors.rightMargin: 16
                anchors.verticalCenter: parent.verticalCenter
                text: root.detailsOpen ? "−" : "+"; color: Design.purpleBright
                font.family: Design.fontFamily; font.pixelSize: 19
            }
            MouseArea {
                id: detailsMouse; anchors.fill: parent; hoverEnabled: true
                cursorShape: Qt.PointingHandCursor; onClicked: parent.trigger()
            }
        }

        Rectangle {
            visible: root.detailsOpen
            x: 0; y: 350; width: 478; height: 244; radius: 12
            color: Design.surface; border.width: 1; border.color: Design.border
            Column {
                x: 16; y: 13; width: 446; spacing: 8
                Repeater {
                    model: [
                        ["Contract", action.shortContract || ""],
                        ["Calldata SHA-256", action.calldataHash || ""],
                        ["Nonce / Gas", (action.nonce || "") + "  /  " + (action.gas || "")],
                        ["Observed block", action.block || ""],
                        ["Max fee per gas", (action.maxFeePerGas || "") + " wei"],
                        ["Priority fee", (action.maxPriorityFeePerGas || "") + " wei"],
                        ["Exact maximum", (action.maxTotalFeeWei || "") + " wei"],
                        ["Action ID", action.shortActionId || ""],
                        ["Digest", action.shortDigest || ""]
                    ]
                    Item {
                        required property var modelData
                        width: 446; height: 17
                        Text {
                            text: parent.modelData[0]; color: Design.textFaint
                            font.family: Design.fontFamily; font.pixelSize: 9
                        }
                        Text {
                            anchors.right: parent.right; width: 285
                            horizontalAlignment: Text.AlignRight; elide: Text.ElideMiddle
                            text: parent.modelData[1]; color: Design.text
                            font.family: Design.fontFamily; font.pixelSize: 9
                        }
                    }
                }
            }
        }

        FormButton {
            objectName: "continueOfflineSigningButton"
            x: 68; y: root.detailsOpen ? 612 : 356; width: 342; height: 55
            label: "Continue to sign"
            primary: walletController.offlineSigningAvailable
            controlEnabled: walletController.offlineSigningAvailable
            onTriggered: walletController.beginOfflineSigning()
        }
        Text {
            x: 24; y: root.detailsOpen ? 674 : 417; width: 430
            horizontalAlignment: Text.AlignHCenter
            text: walletController.offlineSigningGateMessage
            color: walletController.offlineSigningAvailable ? Design.textFaint : "#FF91AF"
            font.family: Design.fontFamily; font.pixelSize: 9
        }
        FormButton {
            objectName: "editTransferButton"
            x: 68; y: root.detailsOpen ? 698 : 440; width: 342; height: 44
            label: "Edit recipient"; controlEnabled: true; primary: false
            onTriggered: walletController.editTransfer()
        }
    }
}
