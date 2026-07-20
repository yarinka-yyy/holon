import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    objectName: "walletContent"
    width: 514
    height: 686
    property real sceneScale: Math.min(width / 514, height / 686)

    Rectangle {
        anchors.fill: parent
        radius: Math.max(10, 13 * root.sceneScale)
        color: Design.backgroundDeep
        border.width: 1
        border.color: "#30364B"
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#071021" }
            GradientStop { position: 0.48; color: "#050A19" }
            GradientStop { position: 1.0; color: "#020511" }
        }
    }

    Item {
        id: stage
        width: 514
        height: 686
        x: (root.width - width * scale) / 2
        y: (root.height - height * scale) / 2
        scale: root.sceneScale
        transformOrigin: Item.TopLeft

        MainPage {
            id: mainPage
            objectName: "mainPage"
            anchors.fill: parent
            enabled: walletController.currentScreen === "main"
            visible: opacity > 0.01
            opacity: enabled ? 1 : 0
            Behavior on opacity {
                NumberAnimation { duration: Design.normalMotion; easing.type: Easing.OutCubic }
            }
        }
        WalletsPage {
            id: walletsPage
            objectName: "walletsPage"
            anchors.fill: parent
            enabled: walletController.currentScreen === "wallets"
            visible: opacity > 0.01
            opacity: enabled ? 1 : 0
            Behavior on opacity {
                NumberAnimation { duration: Design.normalMotion; easing.type: Easing.OutCubic }
            }
        }
        Chrome { anchors.fill: parent; window: walletWindow; z: 50 }
    }

    ResizeFrame { anchors.fill: parent; window: walletWindow; z: 100 }
}
