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
        x: 100; y: 148; spacing: 8
        NetworkCard {
            objectName: "receiveEthereum"; width: 153; height: 44
            label: "Ethereum"; iconSource: "assets/ethereum.svg"
            selected: walletController.receiveNetwork === "ethereum"
            onTriggered: walletController.selectReceiveNetwork("ethereum")
        }
        NetworkCard {
            objectName: "receiveBase"; width: 153; height: 44
            label: "Base"; iconSource: "assets/base.png"
            selected: walletController.receiveNetwork === "base"
            onTriggered: walletController.selectReceiveNetwork("base")
        }
    }
    SurfaceCard {
        x: 96; y: 220; width: 322; height: 322
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
        x: 56; y: 574; width: 402; horizontalAlignment: Text.AlignHCenter
        text: walletController.activeProfile.label || "Account"
        color: Design.text; font.family: Design.fontFamily; font.pixelSize: 18
        font.weight: Font.DemiBold
    }
    Text {
        objectName: "receiveAddress"; x: 56; y: 612; width: 402
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WrapAnywhere
        text: walletController.activeProfile.address || ""
        color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
    }
    FormButton {
        id: copyReceiveAddress
        objectName: "copyReceiveAddress"; x: 72; y: 684; width: 370; height: 56
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
        x: 56; y: 760; width: 402; horizontalAlignment: Text.AlignHCenter
        text: "Ethereum and Base use the same EVM address"
        color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 11
    }
}
