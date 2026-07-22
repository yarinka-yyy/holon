import QtQuick
import "."

Item {
    id: root
    property string title: "Setting"
    property string subtitle: ""
    property url iconSource
    signal triggered()
    function trigger() { triggered() }
    SurfaceCard { anchors.fill: parent; interactive: true; onTriggered: root.triggered() }
    Rectangle {
        x: 16; anchors.verticalCenter: parent.verticalCenter
        width: 44; height: 44; radius: 14; color: Design.surfaceSecondary
        Image {
            anchors.centerIn: parent; width: 24; height: 24
            source: root.iconSource; sourceSize: Qt.size(48, 48)
        }
    }
    Text {
        x: 76; y: 16; text: root.title; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 16; font.weight: Font.Medium
    }
    Text {
        x: 76; y: 42; width: parent.width - 116; text: root.subtitle
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        elide: Text.ElideRight
    }
    Text {
        anchors.right: parent.right; anchors.rightMargin: 18
        anchors.verticalCenter: parent.verticalCenter; text: "›"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 24
    }
}
