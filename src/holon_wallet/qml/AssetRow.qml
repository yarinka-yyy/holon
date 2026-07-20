import QtQuick
import "."

Item {
    id: root
    property string assetName: "Ethereum"
    property string symbol: "ETH"
    property string chain: "Ethereum"
    property url iconSource
    property bool divider: true

    Image {
        x: 16
        anchors.verticalCenter: parent.verticalCenter
        width: 36
        height: 36
        source: root.iconSource
        sourceSize: Qt.size(72, 72)
    }

    Text {
        x: 66
        y: 10
        text: root.assetName
        color: Design.text
        font.family: Design.fontFamily
        font.pixelSize: 14
        font.weight: Font.DemiBold
    }

    Text {
        x: 66
        y: 30
        text: root.symbol
        color: Design.textMuted
        font.family: Design.fontFamily
        font.pixelSize: 11
    }

    Text {
        anchors.right: parent.right
        anchors.rightMargin: 18
        y: 11
        text: "Data unavailable"
        color: Design.textMuted
        font.family: Design.fontFamily
        font.pixelSize: 11
    }

    Text {
        anchors.right: parent.right
        anchors.rightMargin: 18
        y: 31
        text: root.chain
        color: Design.textFaint
        font.family: Design.fontFamily
        font.pixelSize: 9
    }

    Rectangle {
        visible: root.divider
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Design.borderSoft
    }
}
