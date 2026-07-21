import QtQuick
import "."

Item {
    id: root
    property string assetName: "Ethereum"
    property string symbol: "ETH"
    property url iconSource
    property bool divider: true
    property string selectedNetwork: "all"
    property string ethereumValue: "Data unavailable"
    property string ethereumStatus: "UNAVAILABLE"
    property string baseValue: "Data unavailable"
    property string baseStatus: "UNAVAILABLE"

    function valueColor(status) {
        if (status === "LIVE") return Design.text
        if (status === "SIMULATED") return Design.purpleBright
        return Design.textMuted
    }

    Image {
        x: 16
        anchors.verticalCenter: parent.verticalCenter
        width: 36
        height: 36
        source: root.iconSource
        sourceSize: Qt.size(72, 72)
    }

    Text {
        x: 66; y: 10
        text: root.assetName
        color: Design.text
        font.family: Design.fontFamily
        font.pixelSize: 14
        font.weight: Font.DemiBold
    }

    Text {
        x: 66; y: 30
        text: root.symbol
        color: Design.textMuted
        font.family: Design.fontFamily
        font.pixelSize: 11
    }

    Column {
        visible: root.selectedNetwork === "all"
        anchors.right: parent.right
        anchors.rightMargin: 18
        anchors.verticalCenter: parent.verticalCenter
        spacing: 4
        Text {
            anchors.right: parent.right
            text: "Ethereum   " + root.ethereumValue
            color: root.valueColor(root.ethereumStatus)
            font.family: Design.fontFamily; font.pixelSize: 10
        }
        Text {
            anchors.right: parent.right
            text: "Base   " + root.baseValue
            color: root.valueColor(root.baseStatus)
            font.family: Design.fontFamily; font.pixelSize: 10
        }
    }

    Column {
        visible: root.selectedNetwork !== "all"
        anchors.right: parent.right
        anchors.rightMargin: 18
        anchors.verticalCenter: parent.verticalCenter
        spacing: 3
        Text {
            anchors.right: parent.right
            text: root.selectedNetwork === "ethereum"
                ? root.ethereumValue : root.baseValue
            color: root.valueColor(root.selectedNetwork === "ethereum"
                ? root.ethereumStatus : root.baseStatus)
            font.family: Design.fontFamily; font.pixelSize: 12
        }
        Text {
            anchors.right: parent.right
            text: (root.selectedNetwork === "ethereum" ? "Ethereum" : "Base")
                + "  ·  "
                + (root.selectedNetwork === "ethereum"
                    ? root.ethereumStatus : root.baseStatus)
            color: Design.textFaint
            font.family: Design.fontFamily; font.pixelSize: 9
        }
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
