import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    property bool selectorOpen: false

    onEnabledChanged: {
        if (enabled)
            recipientInput.text = walletController.transferRecipient
    }

    BackButton {
        objectName: "sendBackButton"; x: 22; y: 42
        onTriggered: walletController.cancelTransfer()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 49
        text: "Send"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Text {
        x: 24; y: 94; text: "FROM ACCOUNT"
        color: Design.textFaint; font.family: Design.fontFamily
        font.pixelSize: 9; font.letterSpacing: 0.5
    }
    AccountCard {
        objectName: "sendAccountCard"
        x: 18; y: 112; width: 478; height: 86
        profile: walletController.activeProfile
        interactive: !walletController.transferPreparing
        onClicked: root.selectorOpen = !root.selectorOpen
    }

    Text {
        x: 24; y: 216; text: "Recipient"
        color: Design.text; font.family: Design.fontFamily
        font.pixelSize: 14; font.weight: Font.DemiBold
    }
    Rectangle {
        x: 24; y: 242; width: 466; height: 60; radius: 12
        color: recipientInput.activeFocus ? Design.surfaceHover : Design.surface
        border.width: 1
        border.color: recipientInput.activeFocus ? Design.purple : Design.border
        Behavior on border.color { ColorAnimation { duration: Design.fastMotion } }

        TextInput {
            id: recipientInput
            objectName: "transferRecipientInput"
            x: 16; y: 19; width: 362; height: 25
            color: Design.text; selectionColor: Design.purple
            selectedTextColor: "white"; clip: true
            font.family: Design.fontFamily; font.pixelSize: 13
            enabled: !walletController.transferPreparing
            inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
        }
        Text {
            x: 16; anchors.verticalCenter: parent.verticalCenter
            visible: recipientInput.text.length === 0 && !recipientInput.activeFocus
            text: "0x recipient address"
            color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 13
        }
        Item {
            objectName: "pasteRecipientButton"
            anchors.right: parent.right; anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            width: 74; height: 40
            enabled: !walletController.transferPreparing
            function trigger() {
                if (enabled)
                    recipientInput.text = walletController.pasteTransferRecipient()
            }
            Rectangle {
                anchors.fill: parent; radius: 9
                color: pasteMouse.containsMouse ? Design.surfaceHover : Design.surfaceRaised
                border.width: 1; border.color: Design.border
            }
            Image {
                x: 10; anchors.verticalCenter: parent.verticalCenter
                width: 15; height: 15; source: "assets/copy.svg"
                sourceSize: Qt.size(30, 30)
            }
            Text {
                x: 31; anchors.verticalCenter: parent.verticalCenter
                text: "Paste"; color: Design.purpleBright
                font.family: Design.fontFamily; font.pixelSize: 10
            }
            MouseArea {
                id: pasteMouse; anchors.fill: parent; hoverEnabled: true
                enabled: parent.enabled; cursorShape: Qt.PointingHandCursor
                onClicked: parent.trigger()
            }
        }
    }

    Text {
        x: 24; y: 323; text: "Transfer"
        color: Design.text; font.family: Design.fontFamily
        font.pixelSize: 14; font.weight: Font.DemiBold
    }
    Rectangle {
        x: 24; y: 350; width: 466; height: 128; radius: 14
        color: Design.surface; border.width: 1; border.color: Design.border
        GlowWave { x: 200; y: 65; width: 266; height: 63; opacity: 0.25 }

        Rectangle {
            x: 14; y: 14; width: 132; height: 100; radius: 11
            color: Design.surfaceRaised; border.width: 1; border.color: Design.purple
            Image {
                anchors.horizontalCenter: parent.horizontalCenter; y: 13
                width: 32; height: 32; source: "assets/base.svg"
                sourceSize: Qt.size(64, 64)
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter; y: 52
                text: "Base"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 14; font.weight: Font.DemiBold
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter; y: 76
                text: "Chain 8453"; color: Design.textMuted
                font.family: Design.fontFamily; font.pixelSize: 9
            }
        }
        Image {
            x: 172; y: 21; width: 34; height: 34; source: "assets/usdc.svg"
            sourceSize: Qt.size(68, 68)
        }
        Text {
            x: 219; y: 18; text: "1 USDC"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 24; font.weight: Font.DemiBold
        }
        Text {
            x: 219; y: 52; text: "Fixed MVP1 transfer"
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10
        }
        Text {
            x: 172; y: 86
            text: "Available: " + (walletController.baseData.usdcValue || "Data unavailable")
            color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
        }
    }

    Text {
        objectName: "transferErrorLabel"
        x: 32; y: 492; width: 450; height: 32
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
        text: walletController.transferError
        color: "#FF9AA9"; font.family: Design.fontFamily; font.pixelSize: 10
    }

    Item {
        visible: walletController.transferPreparing
        x: 86; y: 526; width: 342; height: 60
        Rectangle {
            anchors.fill: parent; radius: 12
            color: Design.surfaceRaised; border.width: 1; border.color: Design.purple
        }
        Row {
            anchors.centerIn: parent; spacing: 7
            Repeater {
                model: 3
                Rectangle {
                    required property int index
                    width: 7; height: 7; radius: 3.5; color: Design.purpleBright
                    SequentialAnimation on opacity {
                        running: walletController.transferPreparing; loops: Animation.Infinite
                        PauseAnimation { duration: index * 120 }
                        NumberAnimation { to: 0.28; duration: 220 }
                        NumberAnimation { to: 1; duration: 220 }
                        PauseAnimation { duration: (2 - index) * 120 }
                    }
                }
            }
            Text {
                text: "Preparing live data…"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 13
            }
        }
    }
    FormButton {
        objectName: "prepareTransferButton"
        x: 86; y: 526; width: 342; height: 60
        visible: !walletController.transferPreparing
        label: "Prepare 1 USDC"
        controlEnabled: recipientInput.text.trim().length > 0
        onTriggered: walletController.prepareTransfer(recipientInput.text)
    }
    Text {
        x: 48; y: 600; width: 418; height: 42
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
        text: "PublicNode receives sender and recipient during preparation and final revalidation. One signed broadcast occurs only after local confirmation."
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
    }

    AccountSelector {
        anchors.fill: parent; z: 30; open: root.selectorOpen
        onDismissRequested: root.selectorOpen = false
    }
}
