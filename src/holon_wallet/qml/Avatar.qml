import QtQuick
import "."

Item {
    id: root
    property string initials: "A1"
    property bool primary: false

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: "#241A54"
        border.width: 1
        border.color: root.primary ? Design.purpleBright : "#5868E8"
        gradient: Gradient {
            GradientStop { position: 0.0; color: root.primary ? "#A44D9A" : "#393176" }
            GradientStop { position: 0.48; color: root.primary ? "#5C37A5" : "#292C68" }
            GradientStop { position: 1.0; color: "#111C46" }
        }
        layer.enabled: true
        layer.samples: 4
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 5
        radius: width / 2
        color: "transparent"
        border.width: 1
        border.color: "#30FFFFFF"
    }

    Text {
        anchors.centerIn: parent
        text: root.initials
        color: Design.text
        font.family: Design.fontFamily
        font.pixelSize: Math.max(16, root.width * 0.42)
        font.weight: Font.Medium
    }
}
