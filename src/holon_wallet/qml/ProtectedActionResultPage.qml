import QtQuick
import "."

TransactionFlowShell {
    id: root
    property var result: walletController.perpDexResult
    property var presentation: result.presentation || ({})
    property bool funding: result.actionType === "FUND_TRADING_ACCOUNT"
    property bool positive: result.status === "COMPLETED" || result.status === "PENDING_CREDIT"
    title: funding ? "Hyperliquid deposit result" : "Position order result"
    subtitle: presentation.resultSubtitle || "No automatic retry"
    activeStep: 3; backVisible: false

    function phaseLabel() { return funding ? "Deposit to Hyperliquid" : "Position order" }
    function phaseState() {
        if (result.status === "PENDING_CREDIT") return "Sent"
        if (result.status === "COMPLETED") return "Processed"
        if (result.status === "PARTIAL") return "Partly filled"
        if (result.status === "UNKNOWN") return "Needs checking"
        return "Stopped"
    }

    SurfaceCard {
        x: 0; y: 0; width: 458; height: 154
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter; y: 18; width: 60; height: 60; radius: 30
            color: root.positive ? Design.accentSoft : "#332C2020"; border.width: 1
            border.color: root.positive ? Design.accent : Design.warning
            Image { anchors.centerIn: parent; width: 32; height: 32; source: root.positive ? "assets/check.svg" : "assets/warning.svg" }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; y: 92
            text: presentation.resultTitle || "Action result"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 19; font.weight: Font.DemiBold
        }
        Text {
            x: 20; y: 121; width: 418; horizontalAlignment: Text.AlignHCenter
            text: presentation.resultSubtitle || "No automatic retry"; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    Flickable {
        id: resultScroll; x: 0; y: 170; width: 458; height: 226; clip: true
        contentWidth: width; contentHeight: resultColumn.height; boundsBehavior: Flickable.StopAtBounds
        Column {
            id: resultColumn; width: 458; spacing: 10
            SurfaceCard {
                width: 458; height: 64
                Text { x: 14; y: 10; width: 280; text: root.phaseLabel(); color: Design.text; font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium }
                Text { anchors.right: parent.right; anchors.rightMargin: 14; y: 10; text: root.phaseState(); color: root.positive ? Design.accent : Design.warning; font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.DemiBold }
                Text { x: 14; y: 36; width: 430; text: presentation.title || ""; color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11; elide: Text.ElideRight }
            }
            PerpDexTechnicalDetails { width: 458; details: presentation.technicalDetails || [] }
        }
    }
    ScrollCue {
        objectName: "perpDexResultScrollCue"; anchors.right: resultScroll.right; anchors.rightMargin: 8
        anchors.bottom: resultScroll.bottom; anchors.bottomMargin: 8
        suggested: resultScroll.contentHeight > resultScroll.height && resultScroll.contentY < resultScroll.contentHeight - resultScroll.height - 2
    }
    Row {
        visible: (presentation.transactionHash || "").length > 0; x: 0; y: 410; width: 458; spacing: 10
        FormButton {
            objectName: "copyArbitrumTransactionHash"; width: 224; height: 46; primary: false; label: "Copy transaction hash"
            onTriggered: walletController.copyArbitrumTransactionHash(presentation.transactionHash || "")
        }
        FormButton {
            objectName: "openArbitrumTransaction"; width: 224; height: 46; primary: false; label: "Open in Arbiscan"
            onTriggered: walletController.openArbitrumTransaction(presentation.transactionHash || "")
        }
    }
    FormButton {
        objectName: "perpDexResultDoneButton"; x: 0; y: 528; width: 458; height: 56; label: "Done"
        onTriggered: walletController.finishPerpDexExecution()
    }
}
