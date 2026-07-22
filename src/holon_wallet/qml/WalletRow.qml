import QtQuick
import "."

Item {
    id: root
    property var profile: ({})
    property bool active: false
    signal selected(string profileId)
    SurfaceCard {
        anchors.fill: parent; interactive: true; selected: root.active
        onTriggered: root.selected(root.profile.id)
    }
    Avatar {
        x: 16; anchors.verticalCenter: parent.verticalCenter
        width: 52; height: 52; initials: root.profile.initials || "A"
        primary: root.active
    }
    Text {
        x: 84; y: 16; width: 200; text: root.profile.label || "Account"
        color: Design.text; font.family: Design.fontFamily
        font.pixelSize: 16; font.weight: Font.DemiBold; elide: Text.ElideRight
    }
    Text {
        x: 84; y: 43; text: root.profile.shortAddress || ""
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
    }
    Rectangle {
        anchors.right: parent.right; anchors.rightMargin: 18; y: 15
        width: root.active ? 58 : 84; height: 25; radius: 10
        color: root.active ? Design.accentSoft : Design.surfaceSecondary
        border.width: 1; border.color: root.active ? Design.accent : Design.border
        Text {
            anchors.centerIn: parent
            text: root.active ? "Active" : (root.profile.typeLabel || "Account")
            color: root.active ? Design.accent : Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
        }
    }
    Text {
        anchors.right: parent.right; anchors.rightMargin: 20; y: 47
        text: "›"; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 21
    }
}
