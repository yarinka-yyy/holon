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
    }
    ResizeFrame { anchors.fill: parent; window: walletWindow; z: 100 }
}
