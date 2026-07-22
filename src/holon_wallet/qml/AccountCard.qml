import QtQuick
import "."

SurfaceCard {
    id: root
    property var profile: ({})
    signal receiveRequested()
    signal copyRequested()
    signal selectorRequested()

    Rectangle {
        x: 16; anchors.verticalCenter: parent.verticalCenter
        width: 62; height: 62; radius: 31
        gradient: Gradient {
            GradientStop { position: 0; color: "#637A78" }
            GradientStop { position: 0.55; color: "#34464A" }
            GradientStop { position: 1; color: "#202C32" }
        }
        Text {
            anchors.centerIn: parent; text: root.profile.initials || "A"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 24; font.weight: Font.DemiBold
        }
    }
    Text {
        x: 96; y: 21; width: parent.width - 164
        text: root.profile.label || "Account"; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 18; font.weight: Font.DemiBold
        elide: Text.ElideRight
    }
    Text {
        x: 96; y: 53; text: root.profile.shortAddress || ""; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 14
    }
    Item {
        objectName: "accountCopyButton"
        x: 275; y: 43; width: 38; height: 38
        function trigger() { root.copyRequested() }
        Rectangle {
            anchors.fill: parent; radius: 10
            color: copyMouse.containsMouse ? Design.surfaceHover : "transparent"
        }
        Image {
            anchors.centerIn: parent; width: 19; height: 19
            source: "assets/copy.svg"; sourceSize: Qt.size(38, 38)
        }
        MouseArea {
            id: copyMouse; anchors.fill: parent; hoverEnabled: true
            cursorShape: Qt.PointingHandCursor; onClicked: parent.trigger()
        }
    }
    Item {
        objectName: "accountSelectorButton"
        anchors.right: parent.right; anchors.rightMargin: 14
        anchors.verticalCenter: parent.verticalCenter; width: 46; height: 46
        function trigger() { root.selectorRequested() }
        Rectangle {
            anchors.fill: parent; radius: 12
            color: arrowMouse.containsMouse ? Design.surfaceHover : "transparent"
        }
        Image {
            anchors.centerIn: parent; width: 20; height: 20
            source: "assets/chevron-down.svg"; sourceSize: Qt.size(40, 40)
        }
        MouseArea {
            id: arrowMouse; anchors.fill: parent; hoverEnabled: true
            cursorShape: Qt.PointingHandCursor; onClicked: parent.trigger()
        }
    }
    MouseArea {
        objectName: "accountReceiveZone"
        x: 0; y: 0; width: 270; height: parent.height
        function trigger() { root.receiveRequested() }
        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
        onClicked: trigger()
    }
}
