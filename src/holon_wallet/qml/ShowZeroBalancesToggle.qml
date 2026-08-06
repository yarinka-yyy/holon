import QtQuick
import "."

Item {
    id: root
    property bool checked: false
    signal toggled(bool checked)

    activeFocusOnTab: true
    Accessible.name: "Show zero balances"
    Accessible.role: Accessible.CheckBox
    Accessible.checkable: true
    Accessible.checked: checked
    Keys.onSpacePressed: trigger()
    Keys.onReturnPressed: trigger()

    function trigger() { toggled(!checked) }

    Row {
        anchors.centerIn: parent; spacing: 8
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: "Show zero balances"
            color: toggleMouse.containsMouse || root.activeFocus
                ? Design.accent : Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 12
        }
        Rectangle {
            id: track
            anchors.verticalCenter: parent.verticalCenter
            width: 32; height: 18; radius: 9
            color: root.checked ? Design.accent : Design.borderStrong
            border.width: root.activeFocus ? 1 : 0
            border.color: Design.accentHover
            Rectangle {
                width: 12; height: 12; radius: 6
                y: 3; x: root.checked ? 17 : 3
                color: root.checked ? Design.textOnAccent : Design.text
                Behavior on x { NumberAnimation { duration: Design.fastMotion } }
            }
        }
    }
    MouseArea {
        id: toggleMouse; anchors.fill: parent; hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: { root.forceActiveFocus(); root.trigger() }
    }
}
