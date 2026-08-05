import QtQuick
import "."

PageState {
    id: root
    ScreenHeader {
        objectName: "receive"; x: 28; y: 54; width: 458
        title: "Receive"; subtitle: "Share your public Account address"
        onBackRequested: walletController.showMain()
    }
    Row {
        x: 105; y: 148; spacing: 8
        NetworkCard {
            objectName: "receiveEthereum"; width: 44; height: 44; iconOnly: true
            label: "Ethereum"; iconSource: "assets/ethereum.svg"; iconVisualSize: 22
            selected: walletController.receiveNetwork === "ethereum"
            onTriggered: walletController.selectReceiveNetwork("ethereum")
        }
        NetworkCard {
            objectName: "receiveBase"; width: 44; height: 44; iconOnly: true
            label: "Base"; iconSource: "assets/base.png"; iconVisualSize: 21
            selected: walletController.receiveNetwork === "base"
            onTriggered: walletController.selectReceiveNetwork("base")
        }
        NetworkCard {
            objectName: "receiveArbitrum"; width: 44; height: 44; iconOnly: true
            label: "Arbitrum One"; iconSource: "assets/arbitrum.png"; iconVisualSize: 26
            selected: walletController.receiveNetwork === "arbitrum"
            onTriggered: walletController.selectReceiveNetwork("arbitrum")
        }
        NetworkCard {
            objectName: "receiveOptimism"; width: 44; height: 44; iconOnly: true
            label: "OP Mainnet"; iconSource: "assets/op.png"; iconVisualSize: 24
            selected: walletController.receiveNetwork === "optimism"
            onTriggered: walletController.selectReceiveNetwork("optimism")
        }
        NetworkCard {
            objectName: "receivePolygon"; width: 44; height: 44; iconOnly: true
            label: "Polygon"; iconSource: "assets/polygon.svg"; iconVisualSize: 24
            selected: walletController.receiveNetwork === "polygon"
            onTriggered: walletController.selectReceiveNetwork("polygon")
        }
        NetworkCard {
            objectName: "receiveBsc"; width: 44; height: 44; iconOnly: true
            label: "BNB Smart Chain"; iconSource: "assets/bnb.png"; iconVisualSize: 24
            selected: walletController.receiveNetwork === "bsc"
            onTriggered: walletController.selectReceiveNetwork("bsc")
        }
    }
    Text {
        x: 56; y: 202; width: 402; horizontalAlignment: Text.AlignHCenter
        text: ({"ethereum": "Ethereum", "base": "Base", "arbitrum": "Arbitrum One", "optimism": "OP Mainnet", "polygon": "Polygon", "bsc": "BNB Smart Chain"})[walletController.receiveNetwork] || "EVM network"
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
    }
    SurfaceCard {
        x: 96; y: 236; width: 322; height: 322
        Rectangle {
            anchors.centerIn: parent; width: 270; height: 270; radius: 18
            color: "#F4F7F6"
            Image {
                anchors.centerIn: parent; width: 246; height: 246
                source: walletController.receiveQrSource
                sourceSize: Qt.size(492, 492); cache: false; smooth: false
            }
        }
    }
    Text {
        x: 56; y: 590; width: 402; horizontalAlignment: Text.AlignHCenter
        text: walletController.activeProfile.label || "Account"
        color: Design.text; font.family: Design.fontFamily; font.pixelSize: 18
        font.weight: Font.DemiBold
    }
    Text {
        objectName: "receiveAddress"; x: 56; y: 628; width: 402
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WrapAnywhere
        text: walletController.activeProfile.address || ""
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
    }
    FormButton {
        id: copyReceiveAddress
        objectName: "copyReceiveAddress"; x: 72; y: 700; width: 370; height: 56
        label: "Copy Address"
        onTriggered: {
            if (walletController.copyActiveAddress())
                receiveCopiedFeedback.show()
        }
    }
    CopyFeedback {
        id: receiveCopiedFeedback; objectName: "receiveCopiedFeedback"
        x: copyReceiveAddress.x + copyReceiveAddress.width - width - 12
        y: copyReceiveAddress.y + (copyReceiveAddress.height - height) / 2
        z: 3
    }
    Text {
        x: 56; y: 774; width: 402; horizontalAlignment: Text.AlignHCenter
        text: "All supported EVM networks use the same Account address"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
