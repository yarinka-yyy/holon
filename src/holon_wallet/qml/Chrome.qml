import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: chrome
    required property var window

    MouseArea {
        id: dragArea
        objectName: "windowDragArea"
        x: 8
        y: 6
        width: parent.width - 92
        height: 29
        acceptedButtons: Qt.LeftButton
        onPressed: chrome.window.startSystemMove()
    }

    Rectangle {
        id: minimizeButton
        objectName: "minimizeButton"
        x: parent.width - 76
        y: 7
        width: 30
        height: 26
        radius: 7
        opacity: walletController.canCloseWallet ? 1 : 0.35
        color: minimizeMouse.containsMouse ? "#162039" : "transparent"
        Behavior on color { ColorAnimation { duration: Design.fastMotion } }

        Rectangle {
            anchors.centerIn: parent
            width: 10
            height: 1
            color: minimizeMouse.containsMouse ? Design.text : Design.textMuted
        }
        MouseArea {
            id: minimizeMouse
            anchors.fill: parent
            hoverEnabled: true
            onClicked: chrome.window.showMinimized()
        }
    }

    Rectangle {
        id: closeButton
        objectName: "closeButton"
        x: parent.width - 42
        y: 7
        width: 30
        height: 26
        radius: 7
        color: closeMouse.containsMouse ? "#B53A58" : "transparent"
        Behavior on color { ColorAnimation { duration: Design.fastMotion } }

        Item {
            anchors.centerIn: parent
            width: 10
            height: 10
            rotation: 45
            Rectangle { anchors.centerIn: parent; width: 11; height: 1; color: Design.text }
            Rectangle { anchors.centerIn: parent; width: 1; height: 11; color: Design.text }
        }
        MouseArea {
            id: closeMouse
            anchors.fill: parent
            hoverEnabled: true
            enabled: walletController.canCloseWallet
            onClicked: chrome.window.close()
        }
    }
}
