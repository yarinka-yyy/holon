import QtQuick
import "."

// qmllint disable unqualified

Item {
    id: root
    objectName: "accountSelector"
    property bool open: false
    signal dismissRequested()

    visible: opacity > 0.01
    enabled: root.open
    opacity: root.open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Design.normalMotion } }

    Rectangle {
        anchors.fill: parent
        color: "#88020714"
    }
    MouseArea {
        anchors.fill: parent
        onClicked: root.dismissRequested()
    }

    Rectangle {
        x: 18
        y: 202
        width: 478
        height: 158
        radius: 13
        color: "#F20A1122"
        border.width: 1
        border.color: Design.purple

        Repeater {
            model: walletController.profiles
            delegate: Item {
                required property var modelData
                required property int index
                width: 476
                height: 52
                y: index * 52
                objectName: "profileOption_" + modelData.id
                function trigger() {
                    walletController.selectProfile(modelData.id)
                    root.dismissRequested()
                }

                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 3
                    radius: 9
                    color: optionMouse.containsMouse ? Design.surfaceHover : "transparent"
                    Behavior on color { ColorAnimation { duration: Design.fastMotion } }
                }
                Avatar {
                    x: 13; width: 34; height: 34
                    anchors.verticalCenter: parent.verticalCenter
                    initials: modelData.initials
                    primary: modelData.id === walletController.activeProfileId
                }
                Text {
                    x: 60
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.label
                    color: Design.text
                    font.family: Design.fontFamily
                    font.pixelSize: 13
                    font.weight: Font.Medium
                }
                Rectangle {
                    anchors.right: parent.right
                    anchors.rightMargin: 18
                    anchors.verticalCenter: parent.verticalCenter
                    width: 8; height: 8; radius: 4
                    color: Design.purpleBright
                    visible: modelData.id === walletController.activeProfileId
                }
                MouseArea {
                    id: optionMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: parent.trigger()
                }
            }
        }
    }
}
