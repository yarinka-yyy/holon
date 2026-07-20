import QtQuick

Item {
    id: frame
    required property var window
    property int grip: 6

    function beginResize(edges) {
        frame.window.startSystemResize(edges)
    }

    MouseArea {
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
        width: frame.grip
        cursorShape: Qt.SizeHorCursor
        onPressed: frame.beginResize(Qt.LeftEdge)
    }
    MouseArea {
        anchors { right: parent.right; top: parent.top; bottom: parent.bottom }
        width: frame.grip
        cursorShape: Qt.SizeHorCursor
        onPressed: frame.beginResize(Qt.RightEdge)
    }
    MouseArea {
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: frame.grip
        cursorShape: Qt.SizeVerCursor
        onPressed: frame.beginResize(Qt.TopEdge)
    }
    MouseArea {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: frame.grip
        cursorShape: Qt.SizeVerCursor
        onPressed: frame.beginResize(Qt.BottomEdge)
    }
    MouseArea {
        anchors { left: parent.left; top: parent.top }
        width: 10; height: 10; z: 2
        cursorShape: Qt.SizeFDiagCursor
        onPressed: frame.beginResize(Qt.LeftEdge | Qt.TopEdge)
    }
    MouseArea {
        anchors { right: parent.right; top: parent.top }
        width: 10; height: 10; z: 2
        cursorShape: Qt.SizeBDiagCursor
        onPressed: frame.beginResize(Qt.RightEdge | Qt.TopEdge)
    }
    MouseArea {
        anchors { left: parent.left; bottom: parent.bottom }
        width: 10; height: 10; z: 2
        cursorShape: Qt.SizeBDiagCursor
        onPressed: frame.beginResize(Qt.LeftEdge | Qt.BottomEdge)
    }
    MouseArea {
        anchors { right: parent.right; bottom: parent.bottom }
        width: 10; height: 10; z: 2
        cursorShape: Qt.SizeFDiagCursor
        onPressed: frame.beginResize(Qt.RightEdge | Qt.BottomEdge)
    }
}
