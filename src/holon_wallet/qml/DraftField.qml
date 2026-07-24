import QtQuick
import "."

Item {
    id: root
    property string label: ""
    property string placeholderText: ""
    property string fieldObjectName: ""
    property alias text: input.text
    property bool readOnly: false

    Text {
        x: 0; y: 0; text: root.label; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 12; font.weight: Font.Medium
    }
    Rectangle {
        x: 0; y: 22; width: parent.width; height: 48; radius: Design.controlRadius
        color: Design.surface; border.width: input.activeFocus ? 2 : 1
        border.color: input.activeFocus ? Design.accent : Design.border
        TextInput {
            id: input; objectName: root.fieldObjectName
            x: 14; width: parent.width - 28; anchors.verticalCenter: parent.verticalCenter
            color: Design.text; selectionColor: Design.accentSoft
            selectedTextColor: Design.text; font.family: Design.fontFamily
            font.pixelSize: 14; readOnly: root.readOnly; clip: true
        }
        Text {
            x: 14; anchors.verticalCenter: parent.verticalCenter
            visible: input.text.length === 0 && !input.activeFocus
            text: root.placeholderText; color: Design.textFaint
            font.family: Design.fontFamily; font.pixelSize: 13
        }
    }
}
