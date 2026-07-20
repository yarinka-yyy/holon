import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root

    BackButton {
        id: backButton; objectName: "backButton"; x: 22; y: 42
        onTriggered: walletController.showMain()
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 49
        text: "Wallets"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Text {
        x: 24; y: 99; text: "LOCAL ACCOUNTS  ·  ENCRYPTED VAULT"
        color: Design.purpleBright; font.family: Design.fontFamily
        font.pixelSize: 9; font.letterSpacing: 0.45
    }

    WalletRow {
        objectName: "activeWalletRow"
        x: 18; y: 119; width: 478; height: 104; prominent: true
        profile: walletController.activeProfile; active: true
        onSelected: profileId => walletController.selectProfile(profileId)
    }
    Rectangle {
        objectName: "searchCard"; enabled: false
        x: 18; y: 241; width: 478; height: 51; radius: 12
        color: Design.surface; border.width: 1; border.color: Design.border; opacity: 0.58
        Image {
            x: 17; anchors.verticalCenter: parent.verticalCenter
            width: 22; height: 22; source: "assets/search.svg"; sourceSize: Qt.size(44, 44)
        }
        Text {
            x: 53; anchors.verticalCenter: parent.verticalCenter
            text: "Search wallets"; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 13
        }
    }
    ListView {
        id: walletList
        objectName: "walletList"
        x: 18; y: 307; width: 478; height: 224
        model: walletController.inactiveProfiles
        spacing: 12; clip: true
        delegate: WalletRow {
            required property var modelData
            objectName: "walletRow_" + modelData.id
            width: walletList.width; height: 82
            profile: modelData; active: false
            onSelected: profileId => walletController.selectProfile(profileId)
        }
    }
    Rectangle {
        id: addAccount
        objectName: "addAccount"
        x: 18; y: 548; width: 478; height: 69; radius: 13
        color: addMouse.containsMouse ? Design.surfaceHover : Design.surface
        border.width: 1; border.color: addMouse.containsMouse ? Design.purple : Design.border
        function trigger() { walletController.beginAddPrivateKey() }
        Behavior on color { ColorAnimation { duration: Design.fastMotion } }
        Rectangle {
            x: 18; anchors.verticalCenter: parent.verticalCenter
            width: 42; height: 42; radius: 10
            color: "#0E152A"; border.width: 1; border.color: Design.purple
            Text {
                anchors.centerIn: parent; text: "+"; color: Design.purpleBright
                font.family: Design.fontFamily; font.pixelSize: 27; font.weight: Font.Light
            }
        }
        Text {
            x: 78; anchors.verticalCenter: parent.verticalCenter
            text: "Add New Address"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 15
        }
        MouseArea {
            id: addMouse; anchors.fill: parent; hoverEnabled: true
            cursorShape: Qt.PointingHandCursor; onClicked: addAccount.trigger()
        }
    }
    Text {
        x: 24; y: 645; text: "Active Account is saved locally"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
    }
}
