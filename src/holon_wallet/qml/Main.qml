import QtQuick
import "."

Item {
    id: root
    objectName: "walletContent"
    width: 514; height: 840
    property real sceneScale: Math.min(width / 514, height / 840)

    Rectangle {
        anchors.fill: parent; radius: Math.max(16, 24 * root.sceneScale)
        color: Design.background; border.width: 1; border.color: "#14FFFFFF"
    }
    Item {
        id: stage; width: 514; height: 840
        x: (root.width - width * scale) / 2; y: (root.height - height * scale) / 2
        scale: root.sceneScale; transformOrigin: Item.TopLeft

        WelcomePage { objectName: "welcomePage"; anchors.fill: parent; active: walletController.currentScreen === "welcome" }
        PasswordPage { objectName: "passwordPage"; anchors.fill: parent; active: walletController.currentScreen === "password" }
        ImportPage { objectName: "importPage"; anchors.fill: parent; active: walletController.currentScreen === "import" }
        BackupPage { objectName: "backupPage"; anchors.fill: parent; active: walletController.currentScreen === "backup" }
        MainPage { objectName: "mainPage"; anchors.fill: parent; active: walletController.currentScreen === "main" }
        ReceivePage { objectName: "receivePage"; anchors.fill: parent; active: walletController.currentScreen === "receive" }
        SettingsPage { objectName: "settingsPage"; anchors.fill: parent; active: walletController.currentScreen === "settings" }
        SettingsInfoPage { objectName: "settingsInfoPage"; anchors.fill: parent; active: walletController.currentScreen === "settings_info" }
        RecoveryReviewPage { objectName: "recoveryReviewPage"; anchors.fill: parent; active: walletController.currentScreen === "recovery_review" }
        RecoveryConfirmPage { objectName: "recoveryConfirmPage"; anchors.fill: parent; active: walletController.currentScreen === "recovery_confirm" }
        RecoveryRevealPage { objectName: "recoveryRevealPage"; anchors.fill: parent; active: walletController.currentScreen === "recovery_reveal" }
        ApprovalsPage { objectName: "approvalsPage"; anchors.fill: parent; active: walletController.currentScreen === "approvals" }
        ApprovalReviewPage { objectName: "approvalReviewPage"; anchors.fill: parent; active: walletController.currentScreen === "revoke_review" }
        ApprovalConfirmPage { objectName: "approvalConfirmPage"; anchors.fill: parent; active: walletController.currentScreen === "revoke_confirm" }
        ApprovalSubmitPage { objectName: "approvalSubmitPage"; anchors.fill: parent; active: walletController.currentScreen === "revoke_submit" }
        ApprovalResultPage { objectName: "approvalResultPage"; anchors.fill: parent; active: walletController.currentScreen === "revoke_result" }
        WalletsPage { objectName: "walletsPage"; anchors.fill: parent; active: walletController.currentScreen === "wallets" }
        HistoryPage { objectName: "historyPage"; anchors.fill: parent; active: walletController.currentScreen === "history" }
        TransactionDetailsPage { objectName: "transactionDetailsPage"; anchors.fill: parent; active: walletController.currentScreen === "transaction_details" }
        SendPage { objectName: "sendPage"; anchors.fill: parent; active: walletController.currentScreen === "send" }
        TransferReviewPage { objectName: "transferReviewPage"; anchors.fill: parent; active: walletController.currentScreen === "transfer_review" }
        SignPage { objectName: "mainnetSignPage"; anchors.fill: parent; active: walletController.currentScreen === "sign_transfer" }
        SubmitPage { objectName: "submitPage"; anchors.fill: parent; active: walletController.currentScreen === "submit_transfer" }
        MainnetResultPage { objectName: "mainnetResultPage"; anchors.fill: parent; active: walletController.currentScreen === "transfer_result" }
        UnavailablePage { objectName: "unavailablePage"; anchors.fill: parent; active: walletController.currentScreen === "unavailable" }
        Chrome { anchors.fill: parent; window: walletWindow; z: 50 }

        Rectangle {
            id: guardBanner
            objectName: "guardOpenBanner"
            x: 52; y: 88; width: 410; height: 54; radius: 14; z: 60
            visible: walletController.guardOpenNotice.length > 0
            color: Design.accentSoft; border.width: 1; border.color: Design.accent

            Text {
                anchors.centerIn: parent
                width: parent.width - 32
                text: walletController.guardOpenNotice
                color: Design.text; font.family: Design.fontFamily
                font.pixelSize: 14; font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
            }
        }
    }
    ResizeFrame { anchors.fill: parent; window: walletWindow; z: 100 }
}
