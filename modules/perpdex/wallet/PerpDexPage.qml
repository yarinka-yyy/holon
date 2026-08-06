import QtQuick

Item {
    id: root
    objectName: "perpDexModulePage"
    property var moduleViewModel: ({"title": "PerpDEX", "body": "", "moduleId": "holon.perpdex"})

    Text {
        objectName: "perpDexModuleText"
        anchors.centerIn: parent
        text: root.moduleViewModel.title + "\n" + root.moduleViewModel.body
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        width: parent.width - 56
    }
}
