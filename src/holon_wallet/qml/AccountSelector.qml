import QtQuick
import "."

Item {
    id: root
    objectName: "accountSelector"
    property bool open: false
    signal dismissRequested()
    visible: opacity > 0.01; enabled: open; opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Design.normalMotion } }

    Rectangle { anchors.fill: parent; color: "#990A1014" }
    MouseArea { anchors.fill: parent; onClicked: root.dismissRequested() }
    SurfaceCard {
        x: 28; y: 188; width: 458
        height: Math.min(300, walletController.profiles.length * 66 + 20)
        border.color: Design.borderStrong
        ListView {
            id: list; anchors.fill: parent; anchors.margins: 10
            clip: true; model: walletController.profiles; spacing: 4
            delegate: Item {
                required property var modelData
                objectName: "profileOption_" + modelData.id
                width: list.width; height: 62
                function trigger() {
                    walletController.selectProfile(modelData.id)
                    root.dismissRequested()
                }
                Rectangle {
                    anchors.fill: parent; radius: 12
                    color: optionMouse.containsMouse ? Design.surfaceHover : "transparent"
                }
                Avatar {
                    x: 8; anchors.verticalCenter: parent.verticalCenter; width: 42; height: 42
                    initials: modelData.initials
                    primary: modelData.id === walletController.activeProfileId
                }
                Text {
                    x: 64; y: 11; text: modelData.label; color: Design.text
                    font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.Medium
                }
                Text {
                    x: 64; y: 35; text: modelData.shortAddress; color: Design.textMuted
                    font.family: Design.fontFamily; font.pixelSize: 12
                }
                Rectangle {
                    anchors.right: parent.right; anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    width: 9; height: 9; radius: 5; color: Design.accent
                    visible: modelData.id === walletController.activeProfileId
                }
                MouseArea {
                    id: optionMouse; anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor; onClicked: parent.trigger()
                }
            }
        }
    }
}
