import QtQuick
import "."

PageState {
    id: root
    property string section: walletController.settingsSection
    function pageTitle() {
        if (section === "network") return "Network Data"
        if (section === "security") return "Security"
        return "About"
    }
    function bodyText() {
        if (section === "network")
            return "Ethereum and Base balances are read through approved public RPC endpoints. ETH/USD and USDC/USD use fixed Chainlink feeds on Base. Network data can be unavailable and is never used to authorize a transaction."
        if (section === "security")
            return "Accounts are stored in an encrypted local vault. Every mainnet transfer requires a fresh password and explicit confirmation. Authorization is valid for one exact action only."
        return "Holon Wallet is the standalone MVP1 Wallet for the Holon project. This build supports local Accounts, public portfolio data and bounded ETH/USDC transfers on Ethereum and Base."
    }
    ScreenHeader {
        objectName: "settingsInfo"; x: 28; y: 54; width: 458
        title: parent.pageTitle(); subtitle: "Information"
        onBackRequested: walletController.closeSettingsInfo()
    }
    SurfaceCard {
        x: 28; y: 154; width: 458; height: 260
        Rectangle {
            x: 18; y: 18; width: 48; height: 48; radius: 15
            color: Design.accentSoft
            Image {
                anchors.centerIn: parent; width: 26; height: 26
                source: root.section === "network" ? "assets/network-data.svg"
                    : root.section === "security" ? "assets/lock.svg" : "assets/info.svg"
                sourceSize: Qt.size(52, 52)
            }
        }
        Text {
            x: 18; y: 88; width: parent.width - 36; wrapMode: Text.Wrap
            text: root.bodyText(); color: Design.textMuted
            font.family: Design.fontFamily; font.pixelSize: 14; lineHeight: 1.45
        }
    }
    SettingsRow {
        objectName: "settingsRecoveryMaterial"
        visible: root.section === "security"
        x: 28; y: 438; width: 458; height: 86
        title: "Recovery Material"
        subtitle: "Reveal for " + (walletController.activeProfile.label || "active Account")
        iconSource: "assets/lock.svg"
        onTriggered: walletController.showRecoveryReview()
    }
    Text {
        visible: root.section === "security" && walletController.errorMessage.length > 0
        x: 48; y: 546; width: 418; horizontalAlignment: Text.AlignHCenter
        text: walletController.errorMessage; color: Design.warning
        font.family: Design.fontFamily; font.pixelSize: 12; wrapMode: Text.Wrap
    }
}
