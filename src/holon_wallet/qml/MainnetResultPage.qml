import QtQuick
import "."

TransactionFlowShell {
    id: root
    title: "Transaction Complete"; subtitle: "One-time action result"
    activeStep: 3; backVisible: false
    property var result: walletController.mainnetResult
    property bool positive: result.confirmed === true || result.submitted === true

    SurfaceCard {
        x: 0; y: 0; width: 458; height: 186
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter; y: 20
            width: 64; height: 64; radius: 32
            color: root.positive ? Design.accentSoft : "#332C2020"
            border.width: 1; border.color: root.positive ? Design.accent : Design.danger
            Image {
                id: mainnetStatusSpinner
                anchors.centerIn: parent; width: 34; height: 34
                visible: walletController.receiptChecking
                source: "assets/refresh.svg"
                sourceSize: Qt.size(68, 68)
                RotationAnimation on rotation {
                    running: mainnetStatusSpinner.visible
                    from: 0; to: 360; duration: 850; loops: Animation.Infinite
                }
            }
            Image {
                id: mainnetTerminalIcon
                anchors.centerIn: parent; width: 34; height: 34
                visible: !walletController.receiptChecking
                rotation: 0
                source: root.positive ? "assets/check.svg" : "assets/warning.svg"
                sourceSize: Qt.size(68, 68)
            }
        }
        Text {
            objectName: "mainnetResultTitle"
            anchors.horizontalCenter: parent.horizontalCenter; y: 100
            text: root.result.title || "Transaction result"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 20; font.weight: Font.DemiBold
        }
        Text {
            objectName: "mainnetResultMessage"; x: 24; y: 134; width: 410
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: root.result.message || "No automatic retry will occur."
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
    SurfaceCard {
        objectName: "mainnetProofCard"; x: 0; y: 202; width: 458; height: 216
        visible: (root.result.transactionHash || "").length > 0
        Text {
            x: 16; y: 16; text: "Public status"; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 12
        }
        Text {
            objectName: "mainnetPublicStatus"; anchors.right: parent.right
            anchors.rightMargin: 16; y: 15; text: root.result.statusLabel || "Unknown"
            color: root.result.historyStatus === "confirmed" ? Design.accent
                : root.result.historyStatus === "failed" ? Design.danger : Design.warning
            font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.DemiBold
        }
        Rectangle { x: 16; y: 46; width: 426; height: 1; color: "#0FFFFFFF" }
        Text {
            x: 16; y: 61; text: "Recovered signer"; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 11
        }
        Text {
            objectName: "mainnetRecoveredSigner"; x: 16; y: 83; width: 426
            elide: Text.ElideMiddle; text: root.result.recoveredSigner || ""
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
        }
        Text {
            x: 16; y: 119; text: "Transaction hash"; color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 11
        }
        Text {
            objectName: "mainnetTransactionHash"; x: 16; y: 141; width: 426
            elide: Text.ElideMiddle; text: root.result.transactionHash || ""
            color: Design.text; font.family: Design.fontFamily; font.pixelSize: 12
        }
        Image {
            x: 116; y: 178; width: 16; height: 16; visible: walletController.receiptChecking
            source: "assets/refresh.svg"; sourceSize: Qt.size(32, 32)
            RotationAnimation on rotation {
                running: walletController.receiptChecking
                from: 0; to: 360; duration: 850; loops: Animation.Infinite
            }
        }
        Text {
            objectName: "receiptTrackingLabel"; x: 136; y: 179; width: 306
            text: walletController.receiptChecking ? "Checking transaction status…"
                : (root.result.broadcastAttempted ? "Broadcast attempted exactly once" : "Nothing was broadcast")
            color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 10
        }
    }
    FormButton {
        objectName: "checkMainnetStatusButton"; x: 0; y: 464; width: 458; height: 48
        visible: Boolean(root.result.canCheckStatus)
        label: walletController.receiptChecking ? "Checking status…" : "Check again"
        primary: false; controlEnabled: !walletController.receiptChecking
        onTriggered: walletController.checkMainnetStatus(root.result.actionId || "")
    }
    FormButton {
        objectName: "mainnetResultDoneButton"; x: 0; y: 528
        width: 458; height: 56; label: "Done"
        onTriggered: walletController.finishMainnetExecution()
    }
}
