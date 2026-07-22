import QtQuick
import "."

PageState {
    id: root
    property string selectedNetwork: ""
    property string selectedAsset: ""
    property string maximumAmount: selectedNetwork && selectedAsset
        ? walletController.maximumTransferAmount(selectedNetwork, selectedAsset) : ""

    function restoreDraft() {
        selectedNetwork = walletController.transferNetwork
        selectedAsset = walletController.transferAsset
        recipientInput.text = walletController.transferRecipient
        amountInput.text = walletController.transferAmountInput
        assetSelector.menuOpen = false
    }
    function chooseNetwork(value) {
        if (selectedNetwork === value) return
        selectedNetwork = value
        selectedAsset = ""
        amountInput.clear()
        assetSelector.menuOpen = false
    }
    function chooseAsset(value) {
        if (selectedAsset !== value) {
            selectedAsset = value
            amountInput.clear()
        }
        assetSelector.menuOpen = false
    }
    function availableBalance() {
        if (!selectedNetwork || !selectedAsset) return "Select network and token"
        let data = selectedNetwork === "ethereum"
            ? walletController.ethereumData : walletController.baseData
        return selectedAsset === "eth" ? data.ethValue : data.usdcValue
    }
    function applyMaximum() {
        walletController.requestMaximumTransfer(
            selectedNetwork, selectedAsset, recipientInput.text
        )
    }
    onEnabledChanged: if (enabled) restoreDraft()

    Connections {
        target: walletController
        function onTransferMaximumReady(networkId, assetId, recipient, amount) {
            if (root.enabled && root.selectedNetwork === networkId
                    && root.selectedAsset === assetId
                    && recipientInput.text === recipient) {
                amountInput.text = amount
            }
        }
    }

    ScreenHeader {
        objectName: "send"; x: 28; y: 54; width: 458
        title: "Send"; subtitle: "Ethereum or Base · ETH or USDC"
        onBackRequested: walletController.cancelTransfer()
    }

    Flickable {
        id: formScroll; objectName: "sendFormScroll"
        x: 28; y: 132; width: 458; height: 678
        contentWidth: width; contentHeight: 714; clip: true
        boundsBehavior: Flickable.StopAtBounds

        Item {
            objectName: "sendAccountCard"; x: 0; y: 0; width: 458; height: 58
            Avatar {
                x: 4; anchors.verticalCenter: parent.verticalCenter; width: 50; height: 50
                initials: walletController.activeProfile.initials || "A"; primary: true
            }
            Text {
                x: 70; y: 7; text: walletController.activeProfile.label || "Account"
                color: Design.text; font.family: Design.fontFamily; font.pixelSize: 16
                font.weight: Font.Medium
            }
            Text {
                x: 70; y: 34; text: walletController.activeProfile.shortAddress || ""
                color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 12
            }
            Rectangle {
                anchors.right: parent.right; anchors.rightMargin: 4
                anchors.verticalCenter: parent.verticalCenter
                width: 66; height: 30; radius: 10; color: Design.accentSoft
                Text {
                    anchors.centerIn: parent; text: "From"; color: Design.accent
                    font.family: Design.fontFamily; font.pixelSize: 11; font.weight: Font.Medium
                }
            }
        }

        SurfaceCard {
            objectName: "recipientBlock"; x: 0; y: 72; width: 458; height: 116
            Text {
                x: 16; y: 13; text: "To"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.DemiBold
            }
            Rectangle {
                x: 14; y: 42; width: 430; height: 58; radius: Design.controlRadius
                color: Design.surface; border.width: recipientInput.activeFocus ? 2 : 1
                border.color: recipientInput.activeFocus ? Design.accent : Design.border
                Image {
                    x: 14; anchors.verticalCenter: parent.verticalCenter; width: 22; height: 22
                    source: "assets/user.svg"; sourceSize: Qt.size(44, 44)
                }
                TextInput {
                    id: recipientInput; objectName: "transferRecipientInput"
                    x: 48; y: 18; width: 284; height: 24
                    enabled: !walletController.transferPreparing
                        && !walletController.transferMaximumQuoting
                    clip: true
                    color: Design.text; selectionColor: Design.accent
                    selectedTextColor: Design.textOnAccent
                    font.family: Design.fontFamily; font.pixelSize: 14
                    inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
                    onTextChanged: if (!activeFocus) cursorPosition = 0
                }
                Text {
                    x: 48; anchors.verticalCenter: parent.verticalCenter
                    visible: recipientInput.text.length === 0 && !recipientInput.activeFocus
                    text: "Enter or paste address"; color: Design.textFaint
                    font.family: Design.fontFamily; font.pixelSize: 14
                }
                Item {
                    objectName: "pasteRecipientButton"
                    anchors.right: parent.right; anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter; width: 80; height: 42
                    enabled: !walletController.transferPreparing
                        && !walletController.transferMaximumQuoting
                    function trigger() {
                        recipientInput.text = walletController.pasteTransferRecipient()
                    }
                    Rectangle {
                        anchors.fill: parent; radius: 11
                        color: pasteMouse.containsMouse
                            ? Design.surfaceHover : Design.surfaceSecondary
                        border.width: 1; border.color: Design.border
                    }
                    Text {
                        anchors.centerIn: parent; text: "Paste"; color: Design.accent
                        font.family: Design.fontFamily; font.pixelSize: 12
                        font.weight: Font.Medium
                    }
                    MouseArea {
                        id: pasteMouse; anchors.fill: parent; enabled: parent.enabled
                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: parent.trigger()
                    }
                }
            }
        }

        SurfaceCard {
            objectName: "networkBlock"; x: 0; y: 204; width: 458; height: 148
            Text {
                x: 16; y: 13; text: "Select Network"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.DemiBold
            }
            Item {
                id: ethereumTile; objectName: "sendEthereumNetwork"
                x: 14; y: 42; width: 208; height: 90
                property bool selected: root.selectedNetwork === "ethereum"
                enabled: !walletController.transferPreparing
                    && !walletController.transferMaximumQuoting
                function trigger() { if (enabled) root.chooseNetwork("ethereum") }
                SurfaceCard {
                    anchors.fill: parent; radius: 12; interactive: ethereumTile.enabled
                    selected: ethereumTile.selected
                    color: ethereumTile.selected ? Design.accentSoft : Design.surface
                    onTriggered: ethereumTile.trigger()
                }
                Image {
                    anchors.horizontalCenter: parent.horizontalCenter; y: 12
                    width: 34; height: 34; source: "assets/ethereum.svg"
                    sourceSize: Qt.size(68, 68)
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter; y: 55
                    text: "Ethereum"; color: ethereumTile.selected ? Design.accent : Design.textMuted
                    font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
                }
                Image {
                    visible: ethereumTile.selected; x: 176; y: 10; width: 18; height: 18
                    source: "assets/check.svg"; sourceSize: Qt.size(36, 36)
                }
            }
            Item {
                id: baseTile; objectName: "sendBaseNetwork"
                x: 236; y: 42; width: 208; height: 90
                property bool selected: root.selectedNetwork === "base"
                enabled: !walletController.transferPreparing
                    && !walletController.transferMaximumQuoting
                function trigger() { if (enabled) root.chooseNetwork("base") }
                SurfaceCard {
                    anchors.fill: parent; radius: 12; interactive: baseTile.enabled
                    selected: baseTile.selected
                    color: baseTile.selected ? Design.accentSoft : Design.surface
                    onTriggered: baseTile.trigger()
                }
                Image {
                    anchors.horizontalCenter: parent.horizontalCenter; y: 12
                    width: 34; height: 34; source: "assets/base.png"
                    sourceSize: Qt.size(68, 68)
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter; y: 55
                    text: "Base"; color: baseTile.selected ? Design.accent : Design.textMuted
                    font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
                }
                Image {
                    visible: baseTile.selected; x: 176; y: 10; width: 18; height: 18
                    source: "assets/check.svg"; sourceSize: Qt.size(36, 36)
                }
            }
        }

        SurfaceCard {
            id: tokenBlock; objectName: "tokenAmountBlock"
            x: 0; y: 368; width: 458; height: 166; z: assetSelector.menuOpen ? 20 : 1
            Text {
                x: 16; y: 13; text: "Select Token and Amount"; color: Design.text
                font.family: Design.fontFamily; font.pixelSize: 15; font.weight: Font.DemiBold
            }
            Item {
                id: assetSelector; objectName: "assetSelectorButton"
                x: 14; y: 42; width: 154; height: 58
                property bool menuOpen: false
                enabled: !!root.selectedNetwork && !walletController.transferPreparing
                    && !walletController.transferMaximumQuoting
                function trigger() {
                    if (assetSelector.enabled)
                        assetSelector.menuOpen = !assetSelector.menuOpen
                }
                opacity: enabled ? 1 : 0.44
                Rectangle {
                    anchors.fill: parent; radius: Design.controlRadius
                    color: assetMouse.containsMouse ? Design.surfaceHover : Design.surface
                    border.width: 1; border.color: assetSelector.menuOpen ? Design.accent : Design.border
                }
                Image {
                    x: 12; anchors.verticalCenter: parent.verticalCenter; width: 30; height: 30
                    visible: !!root.selectedAsset
                    source: root.selectedAsset === "eth"
                        ? "assets/ethereum.svg" : "assets/usdc.png"
                    sourceSize: Qt.size(60, 60)
                }
                Text {
                    x: root.selectedAsset ? 52 : 14; anchors.verticalCenter: parent.verticalCenter
                    text: root.selectedAsset ? root.selectedAsset.toUpperCase() : "Token"
                    color: Design.text; font.family: Design.fontFamily; font.pixelSize: 14
                    font.weight: Font.Medium
                }
                Image {
                    anchors.right: parent.right; anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter; width: 18; height: 18
                    source: "assets/chevron-down.svg"; sourceSize: Qt.size(36, 36)
                    rotation: assetSelector.menuOpen ? 180 : 0
                    Behavior on rotation { NumberAnimation { duration: Design.fastMotion } }
                }
                MouseArea {
                    id: assetMouse; anchors.fill: parent; enabled: assetSelector.enabled
                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: assetSelector.trigger()
                }
            }
            Rectangle {
                x: 182; y: 42; width: 262; height: 58; radius: Design.controlRadius
                color: Design.surface; border.width: amountInput.activeFocus ? 2 : 1
                border.color: amountInput.activeFocus ? Design.accent : Design.border
                TextInput {
                    id: amountInput; objectName: "transferAmountInput"
                    x: 14; y: 16; width: 160; height: 28
                    enabled: !!root.selectedAsset && !walletController.transferPreparing
                        && !walletController.transferMaximumQuoting
                    clip: true; color: Design.text; selectionColor: Design.accent
                    selectedTextColor: Design.textOnAccent
                    font.family: Design.fontFamily; font.pixelSize: 20
                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                }
                Text {
                    x: 14; anchors.verticalCenter: parent.verticalCenter
                    visible: amountInput.text.length === 0 && !amountInput.activeFocus
                    text: "0.0"; color: Design.textFaint
                    font.family: Design.fontFamily; font.pixelSize: 18
                }
                Text {
                    anchors.right: parent.right; anchors.rightMargin: 14
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.selectedAsset ? root.selectedAsset.toUpperCase() : "Asset"
                    color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 13
                }
            }
            Rectangle {
                x: 14; y: 112; width: 430; height: 1; color: Design.border
            }
            Text {
                x: 16; y: 130; width: 330
                text: "Available: " + root.availableBalance()
                color: Design.textFaint; font.family: Design.fontFamily; font.pixelSize: 12
            }
            Item {
                id: maxButton; objectName: "maxTransferButton"
                x: 370; y: 118; width: 74; height: 38
                property bool controlEnabled: !walletController.transferPreparing
                    && !walletController.transferMaximumQuoting
                    && ((root.selectedAsset === "usdc" && root.maximumAmount.length > 0)
                        || (root.selectedAsset === "eth"
                            && recipientInput.text.length > 0))
                enabled: controlEnabled; opacity: controlEnabled ? 1 : 0.44
                function trigger() { if (controlEnabled) root.applyMaximum() }
                Text {
                    anchors.centerIn: parent
                    text: walletController.transferMaximumQuoting ? "…" : "MAX"
                    color: Design.accent
                    font.family: Design.fontFamily; font.pixelSize: 13; font.weight: Font.DemiBold
                }
                MouseArea {
                    anchors.fill: parent; enabled: maxButton.controlEnabled
                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: maxButton.trigger()
                }
            }

            Rectangle {
                id: assetMenu; objectName: "assetDropdown"
                visible: assetSelector.menuOpen; x: 14; y: 104; width: 154; height: 98; z: 50
                radius: 12; color: Design.surfaceSecondary
                border.width: 1; border.color: Design.borderStrong
                Item {
                    id: ethOption; objectName: "sendEthAsset"
                    width: parent.width; height: 49
                    property bool selected: root.selectedAsset === "eth"
                    function trigger() { root.chooseAsset("eth") }
                    Image {
                        x: 12; anchors.verticalCenter: parent.verticalCenter
                        width: 26; height: 26; source: "assets/ethereum.svg"
                        sourceSize: Qt.size(52, 52)
                    }
                    Text {
                        x: 48; anchors.verticalCenter: parent.verticalCenter; text: "ETH"
                        color: ethOption.selected ? Design.accent : Design.text
                        font.family: Design.fontFamily; font.pixelSize: 13
                    }
                    MouseArea {
                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                        onClicked: ethOption.trigger()
                    }
                }
                Rectangle { x: 10; y: 49; width: 134; height: 1; color: Design.border }
                Item {
                    id: usdcOption; objectName: "sendUsdcAsset"
                    y: 50; width: parent.width; height: 48
                    property bool selected: root.selectedAsset === "usdc"
                    function trigger() { root.chooseAsset("usdc") }
                    Image {
                        x: 12; anchors.verticalCenter: parent.verticalCenter
                        width: 26; height: 26; source: "assets/usdc.png"
                        sourceSize: Qt.size(52, 52)
                    }
                    Text {
                        x: 48; anchors.verticalCenter: parent.verticalCenter; text: "USDC"
                        color: usdcOption.selected ? Design.accent : Design.text
                        font.family: Design.fontFamily; font.pixelSize: 13
                    }
                    MouseArea {
                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                        onClicked: usdcOption.trigger()
                    }
                }
            }
        }

        Text {
            x: 16; y: 550; width: 426; horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: root.selectedAsset === "eth"
                ? "MAX uses live fee data and keeps 10% gas-estimate headroom."
                : "Live balances and local limits are revalidated before authentication."
            color: Design.textFaint; font.family: Design.fontFamily
            font.pixelSize: 11; lineHeight: 1.2
        }
        Text {
            objectName: "transferErrorLabel"; x: 18; y: 590; width: 422
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            text: walletController.transferError; color: Design.danger
            font.family: Design.fontFamily; font.pixelSize: 12
        }
        FormButton {
            objectName: "prepareTransferButton"; x: 44; y: 624; width: 370; height: 56
            label: walletController.transferPreparing ? "Preparing live data…" : "Review transfer"
            controlEnabled: !!root.selectedNetwork && !!root.selectedAsset
                && recipientInput.text.length > 0 && amountInput.text.length > 0
                && !walletController.transferPreparing
                && !walletController.transferMaximumQuoting
            onTriggered: walletController.prepareTransfer(
                root.selectedNetwork,
                root.selectedAsset,
                recipientInput.text,
                amountInput.text
            )
        }
        Row {
            visible: walletController.transferPreparing
            anchors.horizontalCenter: parent.horizontalCenter; y: 694; spacing: 8
            Repeater {
                model: 3
                Rectangle {
                    required property int index
                    width: 7; height: 7; radius: 4; color: Design.accent
                    SequentialAnimation on opacity {
                        running: walletController.transferPreparing; loops: Animation.Infinite
                        PauseAnimation { duration: index * 100 }
                        NumberAnimation { to: 0.25; duration: 200 }
                        NumberAnimation { to: 1; duration: 200 }
                    }
                }
            }
        }
    }
}
