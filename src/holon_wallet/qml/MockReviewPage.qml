import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    property var action: walletController.mockAction

    BackButton {
        objectName: "mockReviewBackButton"; x: 22; y: 42
        onTriggered: walletController.rejectMockAction()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 49
        text: "Review Action"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Rectangle {
        objectName: "mockSimulationBanner"
        x: 24; y: 98; width: 466; height: 38; radius: 10
        color: "#28194F"; border.width: 1; border.color: Design.purple
        Text {
            anchors.centerIn: parent
            text: "SIMULATED ACTION  ·  NOTHING WILL BE SENT"
            color: Design.purpleBright; font.family: Design.fontFamily
            font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 0.45
        }
    }
    Rectangle {
        x: 24; y: 153; width: 466; height: 348; radius: 16
        color: Design.surface; border.width: 1; border.color: Design.border
        GlowWave { x: 196; y: 275; width: 270; height: 73; opacity: 0.35 }

        Text {
            objectName: "mockAccountLabel"
            x: 22; y: 19; text: action.accountLabel || "Account"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 19; font.weight: Font.DemiBold
        }
        Text {
            objectName: "mockSender"
            x: 22; y: 49; text: action.shortSender || ""
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
        Rectangle { x: 20; y: 79; width: 426; height: 1; color: Design.border }

        Text { x: 22; y: 99; text: "Network"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
        Text { objectName: "mockNetwork"; anchors.right: parent.right; anchors.rightMargin: 22; y: 97; text: (action.network || "") + "  ·  " + (action.chainId || ""); color: Design.text; font.family: Design.fontFamily; font.pixelSize: 13 }
        Text { x: 22; y: 137; text: "Amount"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
        Text { objectName: "mockAmount"; anchors.right: parent.right; anchors.rightMargin: 22; y: 133; text: action.amount || ""; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 17; font.weight: Font.DemiBold }
        Text { x: 22; y: 177; text: "Recipient"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
        Text { objectName: "mockRecipient"; anchors.right: parent.right; anchors.rightMargin: 22; y: 175; text: action.recipient || ""; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 11 }
        Text { x: 22; y: 217; text: "Maximum fee"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
        Text { objectName: "mockFee"; anchors.right: parent.right; anchors.rightMargin: 22; y: 215; text: action.feeStatus || ""; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 11 }
        Text { x: 22; y: 257; text: "Action ID"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
        Text { anchors.right: parent.right; anchors.rightMargin: 22; y: 255; text: action.shortActionId || ""; color: Design.text; font.family: Design.fontFamily; font.pixelSize: 11 }
        Text { x: 22; y: 297; text: "Expires"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11 }
        Text { objectName: "mockExpiry"; anchors.right: parent.right; anchors.rightMargin: 22; y: 295; text: action.expiresAt || ""; color: Design.purpleBright; font.family: Design.fontFamily; font.pixelSize: 11 }
    }
    FormButton {
        objectName: "mockContinueButton"
        x: 86; y: 523; width: 342; height: 58
        label: "Continue"; controlEnabled: true
        onTriggered: walletController.continueMockAction()
    }
    FormButton {
        objectName: "mockRejectButton"
        x: 86; y: 595; width: 342; height: 52
        label: "Reject"; controlEnabled: true; primary: false
        onTriggered: walletController.rejectMockAction()
    }
}
