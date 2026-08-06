import QtQuick

Item {
    id: root
    objectName: "mockModulePage"
    property var moduleViewModel: ({"title": "Mock Module", "body": "", "moduleId": "holon.mock"})

    Text {
        objectName: "mockModuleText"
        anchors.centerIn: parent
        text: root.moduleViewModel.title + "\n" + root.moduleViewModel.body
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        width: parent.width - 56
    }
}
