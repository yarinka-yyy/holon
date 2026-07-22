import QtQuick
import "."

PageState {
    ScreenHeader {
        objectName: "settings"; x: 28; y: 54; width: 458
        title: "Settings"; subtitle: "Wallet preferences and information"
        onBackRequested: walletController.showMain()
    }
    SettingsRow {
        objectName: "settingsAccounts"; x: 28; y: 150; width: 458; height: 78
        title: "Accounts"; subtitle: "Select and import local Accounts"
        iconSource: "assets/user.svg"; onTriggered: walletController.showWallets()
    }
    SettingsRow {
        objectName: "settingsNetworkData"; x: 28; y: 242; width: 458; height: 78
        title: "Network Data"; subtitle: "Ethereum, Base and public providers"
        iconSource: "assets/network-data.svg"
        onTriggered: walletController.showSettingsSection("network")
    }
    SettingsRow {
        objectName: "settingsSecurity"; x: 28; y: 334; width: 458; height: 78
        title: "Security"; subtitle: "Vault and one-time authorization model"
        iconSource: "assets/lock.svg"
        onTriggered: walletController.showSettingsSection("security")
    }
    SettingsRow {
        objectName: "settingsAbout"; x: 28; y: 426; width: 458; height: 78
        title: "About"; subtitle: "Holon Wallet MVP1"
        iconSource: "assets/info.svg"
        onTriggered: walletController.showSettingsSection("about")
    }
    SurfaceCard {
        x: 28; y: 542; width: 458; height: 112
        Text {
            x: 18; y: 18; text: "Local-first Wallet"; color: Design.text
            font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.Medium
        }
        Text {
            x: 18; y: 48; width: parent.width - 36; wrapMode: Text.Wrap
            text: "Private material stays encrypted on this device. Public balances and prices are read-only network data."
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
        }
    }
}
