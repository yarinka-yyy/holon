import QtQuick
import "."

Item {
    id: root
    property var profile: ({})
    property bool active: false
    property bool prominent: false
    signal selected(string profileId)

    Rectangle {
        anchors.fill: parent
        radius: 13
        color: rowMouse.containsMouse ? Design.surfaceHover : Design.surface
        border.width: root.active ? 1.7 : 1
        border.color: root.active ? Design.purple : Design.border
        Behavior on color { ColorAnimation { duration: Design.fastMotion } }
        Behavior on border.color { ColorAnimation { duration: Design.normalMotion } }
    }

    GlowWave {
        x: 218; y: 46; width: 260; height: 58
        visible: root.prominent && root.active
        opacity: 0.48
    }

    Avatar {
        x: root.prominent ? 18 : 16
        width: root.prominent ? 66 : 50
        height: width
        anchors.verticalCenter: parent.verticalCenter
        initials: root.profile.initials || "A1"
        primary: root.active
    }

    Text {
        x: root.prominent ? 102 : 82
        y: root.prominent ? 23 : 18
        text: root.profile.label || "Account"
        color: Design.text
        font.family: Design.fontFamily
        font.pixelSize: root.prominent ? 18 : 16
        font.weight: Font.DemiBold
    }

    Text {
        x: root.prominent ? 102 : 82
        y: root.prominent ? 54 : 45
        text: root.profile.shortAddress || ""
        color: Design.textMuted
        font.family: Design.fontFamily
        font.pixelSize: 11
    }

    Image {
        x: root.prominent ? 245 : 218
        y: root.prominent ? 53 : 44
        width: 15; height: 15
        source: "assets/copy.svg"
        sourceSize: Qt.size(30, 30)
        opacity: 0.7
    }

    Text {
        x: root.prominent ? 285 : 275
        y: root.prominent ? 58 : 47
        text: root.profile.typeLabel || ""
        color: Design.textFaint
        font.family: Design.fontFamily
        font.pixelSize: 9
    }

    Rectangle {
        x: root.prominent ? 302 : 278
        y: root.prominent ? 23 : 16
        width: 57; height: 24; radius: 6
        color: "#242F1D58"
        border.width: 1
        border.color: "#7F9A63FF"
        visible: root.active
        Text {
            anchors.centerIn: parent
            text: "Active"
            color: Design.purpleBright
            font.family: Design.fontFamily
            font.pixelSize: 10
            font.weight: Font.DemiBold
        }
    }

    Text {
        anchors.right: parent.right
        anchors.rightMargin: 20
        anchors.verticalCenter: parent.verticalCenter
        text: "›"
        color: rowMouse.containsMouse ? Design.purpleBright : Design.textMuted
        font.family: Design.fontFamily
        font.pixelSize: 30
        font.weight: Font.Light
    }

    MouseArea {
        id: rowMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.selected(root.profile.id)
    }

    scale: rowMouse.pressed ? 0.992 : 1
    Behavior on scale { NumberAnimation { duration: Design.fastMotion } }
}
