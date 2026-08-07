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
        anchors.horizontalCenter: parent.horizontalCenter; y: 14
        width: 34; height: 34; source: root.iconSource; sourceSize: Qt.size(68, 68)
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 63
        text: root.label; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.Medium
    }
}
