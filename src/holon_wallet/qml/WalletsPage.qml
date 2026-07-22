import QtQuick
import "."

PageState {
    id: root
    ScreenHeader {
        objectName: "accounts"; x: 28; y: 54; width: 458
        title: "Accounts"; subtitle: "Encrypted profiles in this Wallet"
        onBackRequested: walletController.closeWallets()
    }
    WalletRow {
        objectName: "activeWalletRow"; x: 28; y: 146; width: 458; height: 84
        profile: walletController.activeProfile; active: true
        onSelected: profileId => walletController.selectProfile(profileId)
    }
    Text {
        x: 28; y: 258; text: "Other Accounts"; color: Design.textMuted
        font.family: Design.fontFamily; font.pixelSize: 14; font.weight: Font.Medium
    }
    ListView {
        id: walletList; objectName: "walletList"
        x: 28; y: 288; width: 458; height: 396
        model: walletController.inactiveProfiles; spacing: 12; clip: true
        delegate: WalletRow {
            required property var modelData
            objectName: "walletRow_" + modelData.id
            width: walletList.width; height: 82; profile: modelData
            onSelected: profileId => walletController.selectProfile(profileId)
        }
    }
    FormButton {
        objectName: "addAccount"; x: 28; y: 716; width: 458; height: 56
        label: "Add Account · Private Key"; primary: false
        onTriggered: walletController.beginAddPrivateKey()
    }
    Text {
        x: 28; y: 793; text: "The active Account is saved locally"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
