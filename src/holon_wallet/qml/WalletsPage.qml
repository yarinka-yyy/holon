import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root

    Rectangle {
        id: backButton
        objectName: "backButton"
        x: 22; y: 42; width: 48; height: 48; radius: 11
        color: backMouse.containsMouse ? Design.surfaceHover : Design.surface
        border.width: 1; border.color: Design.border
        function trigger() { walletController.showMain() }
        Behavior on color { ColorAnimation { duration: Design.fastMotion } }
        Image {
            anchors.centerIn: parent; width: 23; height: 23
            source: "assets/back.svg"; sourceSize: Qt.size(46, 46)
        }
        MouseArea {
            id: backMouse; anchors.fill: parent; hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: backButton.trigger()
        }
    }

    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 49
        text: "Wallets"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 25; font.weight: Font.Bold
    }
    Text {
        x: 24; y: 99; text: "PROTOTYPE  ·  SIMULATED DATA"
        color: Design.purpleBright; font.family: Design.fontFamily
        font.pixelSize: 9; font.letterSpacing: 0.45
    }

    WalletRow {
        objectName: "walletRow_main"
        x: 18; y: 119; width: 478; height: 104; prominent: true
        profile: walletController.profiles[0]
        active: walletController.activeProfileId === profile.id
        onSelected: profileId => walletController.selectProfile(profileId)
    }

    Rectangle {
        objectName: "searchCard"
        enabled: false
        x: 18; y: 241; width: 478; height: 51; radius: 12
        color: Design.surface; border.width: 1; border.color: Design.border
        opacity: 0.58
        Image {
            x: 17; anchors.verticalCenter: parent.verticalCenter
            width: 22; height: 22; source: "assets/search.svg"
            sourceSize: Qt.size(44, 44)
        }
        Text {
            x: 53; anchors.verticalCenter: parent.verticalCenter
            text: "Search wallets"; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 13
        }
    }

    WalletRow {
        objectName: "walletRow_trading"
        x: 18; y: 311; width: 478; height: 82
        profile: walletController.profiles[1]
        active: walletController.activeProfileId === profile.id
        onSelected: profileId => walletController.selectProfile(profileId)
    }
    WalletRow {
        objectName: "walletRow_savings"
        x: 18; y: 408; width: 478; height: 82
        profile: walletController.profiles[2]
        active: walletController.activeProfileId === profile.id
        onSelected: profileId => walletController.selectProfile(profileId)
    }

    Rectangle {
        objectName: "addAccount"
        enabled: false
        x: 18; y: 509; width: 478; height: 69; radius: 13
        color: Design.surface; border.width: 1; border.color: Design.border
        opacity: 0.58
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
            text: "Add New Address"; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 15
        }
    }

    Text {
        x: 24; y: 655; text: "Selection is kept in memory only"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 9
    }
}
