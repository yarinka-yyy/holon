import QtQuick
import "."

Rectangle {
    id: root
    property alias text: field.text
    property string placeholderText: "Enter password"
    property string fieldObjectName: "passwordInput"
    property bool revealed: false
    signal accepted()
    radius: 12
    color: "#7A0B1225"
    border.width: field.activeFocus ? 1.4 : 1
    border.color: field.activeFocus ? Design.purple : Design.border

    function clear() {
        field.clear()
        root.revealed = false
    }

    Image {
        x: 17; anchors.verticalCenter: parent.verticalCenter
        width: 22; height: 22; source: "assets/shield-lock.svg"
        sourceSize: Qt.size(44, 44); opacity: 0.85
    }
    TextInput {
        id: field
        objectName: root.fieldObjectName
        x: 53; width: parent.width - 105
        anchors.verticalCenter: parent.verticalCenter
        color: Design.text; selectionColor: Design.purple
        selectedTextColor: "white"; clip: true
        font.family: Design.fontFamily; font.pixelSize: 15
        echoMode: root.revealed ? TextInput.Normal : TextInput.Password
        passwordMaskDelay: 0
        onAccepted: root.accepted()
    }
    Text {
        x: 53; anchors.verticalCenter: parent.verticalCenter
        visible: field.text.length === 0 && !field.activeFocus
        text: root.placeholderText; color: Design.textFaint
        font.family: Design.fontFamily; font.pixelSize: 15
    }
    Image {
        anchors.right: parent.right; anchors.rightMargin: 17
        anchors.verticalCenter: parent.verticalCenter
        width: 22; height: 22; source: "assets/eye.svg"
        sourceSize: Qt.size(44, 44); opacity: eyeMouse.containsMouse ? 1 : 0.75
        MouseArea {
            id: eyeMouse; anchors.fill: parent; anchors.margins: -8
            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
            onClicked: root.revealed = !root.revealed
        }
    }
}
