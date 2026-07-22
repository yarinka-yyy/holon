import QtQuick
import "."

Item {
    id: root
    property string label: "Network"
    property url iconSource
    property bool selected: false
    property bool controlEnabled: true
    signal triggered()
    enabled: controlEnabled
    function trigger() { if (controlEnabled) triggered() }
    SurfaceCard {
        anchors.fill: parent; radius: 10; interactive: root.controlEnabled
        selected: root.selected; color: root.selected ? Design.accentSoft : Design.surfaceCard
        onTriggered: root.trigger()
    }
    Row {
        anchors.centerIn: parent; spacing: 8
        Image {
            width: 20; height: 20; source: root.iconSource; sourceSize: Qt.size(40, 40)
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter; text: root.label
            color: root.selected ? Design.accent : Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
        }
    }
}
