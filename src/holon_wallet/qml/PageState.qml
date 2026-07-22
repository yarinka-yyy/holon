import QtQuick
Item {
    property bool active: false
    enabled: active; visible: opacity > 0.01; opacity: active ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Design.normalMotion } }
}
