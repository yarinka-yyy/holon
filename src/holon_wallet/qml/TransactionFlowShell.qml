import QtQuick
import "."

PageState {
    id: root
    property string title: "Confirm Transaction"
    property string subtitle: "Base · 1 USDC"
    property var steps: walletController.transactionFlowSteps
    property int activeStep: walletController.transactionFlowStage
    property bool backVisible: true
    signal backRequested()
    default property alias content: contentItem.data

    ScreenHeader {
        x: 28; y: 54; width: 458; title: root.title; subtitle: root.subtitle
        backVisible: root.backVisible; onBackRequested: root.backRequested()
    }
    ProgressStepper {
        objectName: root.objectName + "Progress"; x: 28; y: 132; width: 458
        steps: root.steps; activeStep: root.activeStep
    }
    Item { id: contentItem; x: 28; y: 218; width: 458; height: 592 }
}
