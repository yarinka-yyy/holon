import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root

    Text {
        x: 24; y: 39; text: "Holon Wallet"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter; y: 139
        width: 116; height: 116; radius: 58
        color: walletController.actionResultSuccess ? "#241B52" : "#32182B"
        border.width: 2
        border.color: walletController.actionResultSuccess ? Design.purple : "#FF6688"
        Text {
            anchors.centerIn: parent
            text: walletController.actionResultSuccess ? "✓" : "!"
            color: walletController.actionResultSuccess ? Design.purpleBright : "#FF7F9B"
            font.family: Design.fontFamily; font.pixelSize: 55; font.weight: Font.Light
        }
    }
    Text {
        objectName: "mockResultTitle"
        anchors.horizontalCenter: parent.horizontalCenter; y: 287
        text: walletController.actionResultTitle; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 28; font.weight: Font.DemiBold
    }
    Text {
        objectName: "mockResultMessage"
        x: 72; y: 340; width: 370
        text: walletController.actionResultMessage
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
        color: Design.textMuted; font.family: Design.fontFamily
        font.pixelSize: 13; lineHeight: 1.4
    }
    Rectangle {
        x: 86; y: 423; width: 342; height: 59; radius: 12
        color: Design.surface; border.width: 1; border.color: Design.purple
        Text {
            anchors.centerIn: parent
            text: "SIMULATION ONLY  ·  AUTHORITY LOCKED"
            color: Design.purpleBright; font.family: Design.fontFamily
            font.pixelSize: 9; font.weight: Font.Bold; font.letterSpacing: 0.35
        }
    }
    FormButton {
        objectName: "mockResultDoneButton"
        x: 86; y: 529; width: 342; height: 58
        label: "Done"; controlEnabled: true
        onTriggered: walletController.finishMockResult()
    }
}
