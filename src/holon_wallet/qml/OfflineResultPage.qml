import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    property var result: walletController.offlineSigningResult

    Text {
        x: 24; y: 39; text: "Holon Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Rectangle {
        x: 131; y: 105; width: 252; height: 31; radius: 9
        color: result.simulation ? "#28194F" : (result.success ? "#132D34" : "#321928")
        border.width: 1
        border.color: result.simulation ? Design.purple : (result.success ? "#2E806F" : "#8D3F61")
        Text {
            anchors.centerIn: parent
            text: result.simulation
                ? "SIMULATED TEST RESULT  ·  NOTHING WAS SENT"
                : "OFFLINE RESULT  ·  NOTHING WAS SENT"
            color: result.simulation ? Design.purpleBright : (result.success ? "#76E1C0" : "#FF91AF")
            font.family: Design.fontFamily; font.pixelSize: 9
            font.weight: Font.Bold; font.letterSpacing: 0.3
        }
    }
    Image {
        x: 211; y: 158; width: 92; height: 92
        source: result.success ? "assets/sign-document.svg" : "assets/warning.svg"
        sourceSize: Qt.size(184, 184)
    }
    Text {
        objectName: "offlineResultTitle"
        anchors.horizontalCenter: parent.horizontalCenter; y: 270
        text: result.title || "Offline signing result"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 24; font.weight: Font.DemiBold
    }
    Text {
        objectName: "offlineResultMessage"
        x: 62; y: 312; width: 390; horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap; text: result.message || "Nothing was signed or sent."
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
    }
    Rectangle {
        objectName: "offlineProofCard"
        visible: result.success === true
        x: 44; y: 366; width: 426; height: 152; radius: 14
        color: Design.surface; border.width: 1; border.color: Design.border
        Text { x: 17; y: 16; text: "Recovered signer"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9 }
        Text {
            objectName: "offlineRecoveredSigner"
            x: 17; y: 35; width: 392; elide: Text.ElideMiddle
            text: result.recoveredSigner || ""; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 10
        }
        Rectangle { x: 17; y: 64; width: 392; height: 1; color: Design.borderSoft }
        Text { x: 17; y: 76; text: "Transaction hash · local proof"; color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9 }
        Text {
            objectName: "offlineTransactionHash"
            x: 17; y: 95; width: 392; height: 42
            wrapMode: Text.WrapAnywhere; text: result.transactionHash || ""
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 9
        }
    }
    Text {
        visible: result.success === true
        anchors.horizontalCenter: parent.horizontalCenter; y: 532
        text: "Raw signed data was discarded and cannot be broadcast later"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
    }
    FormButton {
        objectName: "offlineResultDoneButton"
        x: 86; y: result.success === true ? 569 : 450
        width: 342; height: 58; label: "Done"; controlEnabled: true
        onTriggered: walletController.finishOfflineSigning()
    }
}
