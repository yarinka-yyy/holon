import QtQuick
import "."

Item {
    id: root
    property string label: "Action"
    property url iconSource
    property bool controlEnabled: true
    signal triggered()
    enabled: controlEnabled; opacity: controlEnabled ? 1 : 0.44
    function trigger() { if (controlEnabled) triggered() }
    SurfaceCard {
        anchors.fill: parent; interactive: root.controlEnabled
        onTriggered: root.trigger()
    }
    Image {
        anchors.horizontalCenter: parent.horizontalCenter; y: 19
        width: 28; height: 28; source: root.iconSource; sourceSize: Qt.size(56, 56)
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 60
        text: root.label; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.Medium
    }
}
