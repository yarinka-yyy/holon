import QtQuick
import "."

PageState {
    id: root
    onEnabledChanged: if (enabled) recipientInput.text = walletController.transferRecipient
    ScreenHeader {
        objectName: "send"; x: 28; y: 54; width: 458
        title: "Send"; subtitle: "Fixed MVP1 transfer · Base"
        onBackRequested: walletController.cancelTransfer()
    }
    Text {
        x: 28; y: 146; text: "From"; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 13
    }
    SurfaceCard {
        objectName: "sendAccountCard"; x: 28; y: 174; width: 458; height: 78
        Avatar {
            x: 14; anchors.verticalCenter: parent.verticalCenter; width: 48; height: 48
            initials: walletController.activeProfile.initials || "A"; primary: true
        }
        Text {
            x: 78; y: 15; text: walletController.activeProfile.label || "Account"
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 16
            font.weight: Font.Medium
        }
        Text {
            x: 78; y: 42; text: walletController.activeProfile.shortAddress || ""
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        }
        Rectangle {
            anchors.right: parent.right; anchors.rightMargin: 14
            anchors.verticalCenter: parent.verticalCenter
            width: 66; height: 30; radius: 10; color: Design.accentSoft
            Text {
                anchors.centerIn: parent; text: "Active"; color: Design.accent
                font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
            }
        }
    }
    Text {
        x: 28; y: 278; text: "Recipient"; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 13
    }
    Rectangle {
        x: 28; y: 306; width: 458; height: 58; radius: Design.controlRadius
        color: Design.surface; border.width: recipientInput.activeFocus ? 2 : 1
        border.color: recipientInput.activeFocus ? Design.accent : Design.border
        TextInput {
            id: recipientInput; objectName: "transferRecipientInput"
            x: 16; y: 18; width: 348; height: 24
            enabled: !walletController.transferPreparing; clip: true
            color: Design.text; selectionColor: Design.accent; selectedTextColor: Design.textOnAccent
            font.family: Design.fontFamily; font.pixelSize: 14
            inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
        }
        Text {
            x: 16; anchors.verticalCenter: parent.verticalCenter
            visible: recipientInput.text.length === 0 && !recipientInput.activeFocus
            text: "0x recipient address"; color: Design.textFaint
            font.family: Design.fontFamily; font.pixelSize: 14
        }
        Item {
            objectName: "pasteRecipientButton"
            anchors.right: parent.right; anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter; width: 76; height: 42
            enabled: !walletController.transferPreparing
            function trigger() { recipientInput.text = walletController.pasteTransferRecipient() }
            Rectangle {
                anchors.fill: parent; radius: 11
                color: pasteMouse.containsMouse ? Design.surfaceHover : Design.surfaceSecondary
                border.width: 1; border.color: Design.border
            }
            Text {
                anchors.centerIn: parent; text: "Paste"; color: Design.accent
                font.family: Design.fontFamily; font.pixelSize: 12; font.weight: Font.Medium
            }
            MouseArea {
                id: pasteMouse; anchors.fill: parent; enabled: parent.enabled
                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: parent.trigger()
            }
        }
    }
    Text {
        x: 28; y: 390; text: "Transfer"; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 13
    }
    SurfaceCard {
        x: 28; y: 418; width: 458; height: 126
        Image {
            x: 18; y: 20; width: 48; height: 48
            source: "assets/usdc.svg"; sourceSize: Qt.size(96, 96)
        }
        Text {
            x: 82; y: 20; text: "1 USDC"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 24; font.weight: Font.DemiBold
        }
        Text {
            x: 82; y: 57; text: "Fixed amount"; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 12
        }
        Rectangle {
            anchors.right: parent.right; anchors.rightMargin: 18; y: 19
            width: 104; height: 36; radius: 11; color: Design.surfaceSecondary
            Row {
                anchors.centerIn: parent; spacing: 7
                Image { width: 20; height: 20; source: "assets/base.svg"; sourceSize: Qt.size(40, 40) }
                Text {
                    anchors.verticalCenter: parent.verticalCenter; text: "Base"
                    color: Design.text; font.family: Design.fontFamily; font.pixelSize: 13
                }
            }
        }
        Text {
            x: 18; y: 94
            text: "Available: " + (walletController.baseData.usdcValue || "Data unavailable")
            color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
    Rectangle {
        x: 28; y: 568; width: 458; height: 72; radius: Design.controlRadius
        color: Design.surface; border.width: 1; border.color: Design.border
        Image {
            x: 14; y: 14; width: 24; height: 24
            source: "assets/info.svg"; sourceSize: Qt.size(48, 48)
        }
        Text {
            x: 50; y: 12; width: 390; wrapMode: Text.Wrap
            text: "PublicNode receives public sender and recipient for preparation and final revalidation. A signed broadcast happens only after confirmation."
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11; lineHeight: 1.25
        }
    }
    Text {
        objectName: "transferErrorLabel"; x: 52; y: 654; width: 410
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        text: walletController.transferError; color: Design.danger
        font.family: Design.fontFamily; font.pixelSize: 12
    }
    FormButton {
        objectName: "prepareTransferButton"; x: 72; y: 696; width: 370; height: 56
        label: walletController.transferPreparing ? "Preparing live data…" : "Prepare 1 USDC"
        controlEnabled: recipientInput.text.trim().length > 0 && !walletController.transferPreparing
        onTriggered: walletController.prepareTransfer(recipientInput.text)
    }
    Row {
        visible: walletController.transferPreparing
        anchors.horizontalCenter: parent.horizontalCenter; y: 770; spacing: 8
        Repeater {
            model: 3
            Rectangle {
                required property int index
                width: 7; height: 7; radius: 4; color: Design.accent
                SequentialAnimation on opacity {
                    running: walletController.transferPreparing; loops: Animation.Infinite
                    PauseAnimation { duration: index * 100 }
                    NumberAnimation { to: 0.25; duration: 200 }
                    NumberAnimation { to: 1; duration: 200 }
                }
            }
        }
    }
}
