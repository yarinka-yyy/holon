import QtQuick
import QtQuick.Controls
import "."

TransactionFlowShell {
    id: root
    title: "Protected Action Result"
    subtitle: result.status === "PENDING_CREDIT"
        ? "Broadcast is not a Hyperliquid credit · refresh portfolio"
        : "Public reconciliation · no automatic retry"
    activeStep: 3; backVisible: false
    property var result: walletController.perpDexResult
    property bool positive: result.status === "COMPLETED" || result.status === "PENDING_CREDIT"

    SurfaceCard {
        x: 0; y: 0; width: 458; height: 154
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter; y: 18
            width: 60; height: 60; radius: 30
            color: root.positive ? Design.accentSoft : "#332C2020"
            border.width: 1; border.color: root.positive ? Design.accent : Design.warning
            Image {
                anchors.centerIn: parent; width: 32; height: 32
                source: root.positive ? "assets/check.svg" : "assets/warning.svg"
            }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; y: 92
            text: root.result.status || "UNKNOWN"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 19; font.weight: Font.DemiBold
        }
        Text {
            x: 20; y: 121; width: 418; horizontalAlignment: Text.AlignHCenter
            text: root.result.code || "PERPDEX_RESULT_UNKNOWN"
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    Flickable {
        x: 0; y: 170; width: 458; height: 288; clip: true
        contentWidth: width; contentHeight: phaseColumn.height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        Column {
            id: phaseColumn; width: 458; spacing: 9
            Repeater {
                model: root.result.phases || []
                delegate: SurfaceCard {
                    required property var modelData
                    width: 458; height: 66
                    Text {
                        x: 14; y: 10; width: 280; text: modelData.phaseType || "Phase"
                        color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 14; y: 10
                        text: modelData.state || "UNKNOWN"
                        color: modelData.state === "CONFIRMED" ? Design.accent : Design.warning
                        font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold
                    }
                    Text {
                        x: 14; y: 36; width: 430; elide: Text.ElideMiddle
                        text: (modelData.code || "") + (modelData.publicId ? " · " + modelData.publicId : "")
                        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 10
                    }
                }
            }
        }
    }
    FormButton {
        objectName: "perpDexResultDoneButton"; x: 0; y: 528
        width: 458; height: 56; label: "Done"
        onTriggered: walletController.finishPerpDexExecution()
    }
}
