import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    property var result: walletController.mainnetResult
    property bool positive: result.confirmed === true || result.submitted === true

    Text {
        x: 24; y: 39; text: "Holon Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }

    Rectangle {
        x: 131; y: 96; width: 252; height: 31; radius: 9
        color: result.simulation ? "#28194F" : (root.positive ? "#132D34" : "#321928")
        border.width: 1
        border.color: result.simulation ? Design.purple
            : (root.positive ? "#2E806F" : "#8D3F61")
        Text {
            anchors.centerIn: parent
            text: result.simulation
                ? "LOCAL FIXTURE  ·  SIMULATED TEST DATA"
                : "BASE MAINNET  ·  ONE-TIME RESULT"
            color: result.simulation ? Design.purpleBright
                : (root.positive ? "#76E1C0" : "#FF91AF")
            font.family: Design.fontFamily; font.pixelSize: 9
            font.weight: Font.Bold; font.letterSpacing: 0.3
        }
    }
    Image {
        x: 211; y: 143; width: 92; height: 92
        source: root.positive ? "assets/sign-document.svg" : "assets/warning.svg"
        sourceSize: Qt.size(184, 184)
    }
    Text {
        objectName: "mainnetResultTitle"
        anchors.horizontalCenter: parent.horizontalCenter; y: 248
        text: result.title || "Mainnet transfer result"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 24; font.weight: Font.DemiBold
    }
    Text {
        objectName: "mainnetResultMessage"
        x: 54; y: 291; width: 406; height: 43
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
        text: result.message || "No automatic retry will occur."
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
    }
    Rectangle {
        objectName: "mainnetProofCard"
        visible: (result.transactionHash || "").length > 0
        x: 44; y: 350; width: 426; height: 174; radius: 14
        color: Design.surface; border.width: 1; border.color: Design.border
        Text { x: 17; y: 15; text: "Public status"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9 }
        Text {
            objectName: "mainnetPublicStatus"
            anchors.right: parent.right; anchors.rightMargin: 17; y: 14
            text: result.statusLabel || "Unknown"
            color: result.historyStatus === "confirmed" ? "#55D98A"
                : result.historyStatus === "failed" ? "#FF7D91" : "#FFB36D"
            font.family: Design.fontFamily; font.pixelSize: 10; font.weight: Font.DemiBold
        }
        Rectangle { x: 17; y: 39; width: 392; height: 1; color: Design.borderSoft }
        Text { x: 17; y: 51; text: "Recovered signer"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9 }
        Text {
            objectName: "mainnetRecoveredSigner"
            x: 17; y: 69; width: 392; elide: Text.ElideMiddle
            text: result.recoveredSigner || ""; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 10
        }
        Rectangle { x: 17; y: 95; width: 392; height: 1; color: Design.borderSoft }
        Text { x: 17; y: 107; text: "Transaction hash"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9 }
        Text {
            objectName: "mainnetTransactionHash"
            x: 17; y: 125; width: 392; height: 38
            wrapMode: Text.WrapAnywhere; text: result.transactionHash || ""
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 9
        }
    }
    Text {
        objectName: "receiptTrackingLabel"
        x: 54; y: 535; width: 406; horizontalAlignment: Text.AlignHCenter
        text: walletController.receiptChecking
            ? "Checking the public receipt · broadcast will not repeat"
            : (result.broadcastAttempted ? "Broadcast was attempted exactly once" : "Nothing was broadcast")
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
    }
    FormButton {
        objectName: "checkMainnetStatusButton"
        visible: Boolean(result.canCheckStatus)
        x: 86; y: 558; width: 342; height: 46
        label: walletController.receiptChecking ? "Checking status…" : "Check status"
        primary: false; controlEnabled: !walletController.receiptChecking
        onTriggered: walletController.checkMainnetStatus(result.actionId || "")
    }
    FormButton {
        objectName: "mainnetResultDoneButton"
        x: 86; y: Boolean(result.canCheckStatus) ? 614 : 566
        width: 342; height: 52; label: "Done"; controlEnabled: true
        onTriggered: walletController.finishMainnetExecution()
    }
}
