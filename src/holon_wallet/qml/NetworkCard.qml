import QtQuick
import "."

Item {
    id: root
    property string label: "Network"
    property url iconSource
    property bool selected: false
    property bool controlEnabled: true
    property bool iconOnly: false
    property int iconVisualSize: iconOnly ? 24 : 20
    signal triggered()
    enabled: controlEnabled
    activeFocusOnTab: controlEnabled
    Accessible.name: label
    Accessible.role: Accessible.Button
    Keys.onSpacePressed: trigger()
    Keys.onReturnPressed: trigger()
    function trigger() { if (controlEnabled) triggered() }
    SurfaceCard {
        anchors.fill: parent; radius: 10; interactive: root.controlEnabled
        selected: root.selected; color: root.selected ? Design.accentSoft : Design.surfaceCard
        onTriggered: root.trigger()
    }
    Row {
        anchors.centerIn: parent; spacing: 8
        Image {
            width: root.iconVisualSize; height: width
            source: root.iconSource; sourceSize: Qt.size(160, 160)
            fillMode: Image.PreserveAspectFit
            smooth: true; mipmap: true
        }
        Text {
            visible: !root.iconOnly
            anchors.verticalCenter: parent.verticalCenter; text: root.label
            color: root.selected ? Design.accent : Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
        }
    }
    Rectangle {
        visible: root.iconOnly && cardMouse.containsMouse
        z: 20; anchors.top: parent.bottom; anchors.topMargin: 6
        anchors.horizontalCenter: parent.horizontalCenter
        width: tooltipText.implicitWidth + 16; height: 28; radius: 8
        color: Design.surfaceSecondary; border.width: 1; border.color: Design.border
        Text {
            id: tooltipText; anchors.centerIn: parent; text: root.label
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    MouseArea {
        id: cardMouse; anchors.fill: parent; hoverEnabled: true
        cursorShape: Qt.PointingHandCursor; enabled: root.controlEnabled
        onClicked: { root.forceActiveFocus(); root.trigger() }
    }
}
