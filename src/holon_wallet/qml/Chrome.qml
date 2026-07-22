import QtQuick
import "."

Item {
    id: chrome
    required property var window

    MouseArea {
        objectName: "windowDragArea"
        x: 10; y: 6; width: parent.width - 104; height: 32
        acceptedButtons: Qt.LeftButton
        onPressed: chrome.window.startSystemMove()
    }
    Rectangle {
        id: minimizeButton; objectName: "minimizeButton"
        x: parent.width - 88; y: 7; width: 34; height: 28; radius: 9
        color: minMouse.containsMouse ? Design.surfaceHover : "transparent"
        Rectangle { anchors.centerIn: parent; width: 11; height: 1; color: Design.textMuted }
        MouseArea {
            id: minMouse; anchors.fill: parent; hoverEnabled: true
            onClicked: chrome.window.showMinimized()
        }
    }
    Rectangle {
        id: closeButton; objectName: "closeButton"
        x: parent.width - 48; y: 7; width: 34; height: 28; radius: 9
        opacity: walletController.canCloseWallet ? 1 : 0.4
        color: closeMouse.containsMouse ? "#553C292B" : "transparent"
        Item {
            anchors.centerIn: parent; width: 12; height: 12; rotation: 45
            Rectangle { anchors.centerIn: parent; width: 13; height: 1.5; color: Design.textMuted }
            Rectangle { anchors.centerIn: parent; width: 1.5; height: 13; color: Design.textMuted }
        }
        MouseArea {
            id: closeMouse; anchors.fill: parent; hoverEnabled: true
            enabled: walletController.canCloseWallet; onClicked: chrome.window.close()
        }
    }
}
