import QtQuick
import "."

PageState {
    id: page

    ScreenHeader {
        objectName: "moduleHeader"; x: 28; y: 42; width: parent.width - 56
        title: walletController.modulePageData.label || "Module"
        onBackRequested: walletController.showMain()
    }
    Loader {
        id: moduleLoader
        x: 0; y: 126; width: parent.width; height: parent.height - 126
        clip: true
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
