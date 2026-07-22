import QtQuick
import "."

Item {
    id: root
    property string title: ""
    property string subtitle: ""
    property bool backVisible: true
    signal backRequested()
    height: 72
    BackButton {
        objectName: root.objectName.length ? root.objectName + "BackButton" : "screenBackButton"
        visible: root.backVisible; x: 0; y: 0
        onTriggered: root.backRequested()
    }
    Column {
        x: root.backVisible ? 76 : 0; y: root.subtitle.length ? 2 : 13
        width: parent.width - x; spacing: 3
        Text {
            width: parent.width; text: root.title; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 24; font.weight: Font.DemiBold
            elide: Text.ElideRight
        }
        Text {
            visible: root.subtitle.length > 0; width: parent.width
            text: root.subtitle; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 13; elide: Text.ElideRight
        }
    }
}
