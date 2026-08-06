import QtQuick
import "."

PageState {
    id: page

    BackButton {
        x: 28; y: 72
        onTriggered: walletController.showMain()
    }
    Text {
        x: 76; y: 74
        text: walletController.modulePageData.label || "Module"
        color: Design.text; font.family: Design.fontFamily
        font.pixelSize: 22; font.weight: Font.DemiBold
    }
    Loader {
        id: moduleLoader
        x: 0; y: 120; width: parent.width; height: parent.height - 120
        active: page.active && walletController.modulePageAvailable
        source: active ? walletController.modulePageData.qmlUrl : ""
        onLoaded: {
            if (item)
                item.moduleViewModel = walletController.modulePageData.model
        }
    }
    Text {
        anchors.centerIn: parent
        visible: page.active && !walletController.modulePageAvailable
        text: "Optional module is unavailable"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 14
    }
}
