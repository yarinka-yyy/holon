import QtQuick
import "."

Rectangle {
    id: root
    property string initials: "A1"
    property bool primary: false
    radius: width / 2
    border.width: primary ? 1.5 : 1
    border.color: primary ? Design.accent : "#20FFFFFF"
    gradient: Gradient {
        GradientStop { position: 0; color: "#637A78" }
        GradientStop { position: 0.55; color: "#34464A" }
        GradientStop { position: 1; color: "#202C32" }
    }
    Text {
        anchors.centerIn: parent; text: root.initials; color: Design.text
        font.family: Design.fontFamily; font.pixelSize: Math.max(15, root.width * 0.38)
        font.weight: Font.DemiBold
    }
}
