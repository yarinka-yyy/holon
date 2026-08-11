import QtQuick
import "."

Item {
    id: root
    property string amount: ""
    property string account: ""
    property string tokenContract: ""
    property string recipient: ""
    property string maxTotalFeeWei: ""
    width: 458; height: 314

    function shortAddress(value) {
        let text = String(value || "")
        return text.length > 12 ? text.slice(0, 6) + "…" + text.slice(-5) : text
    }

    SurfaceCard {
        width: parent.width; height: 132
        Text {
            x: 16; y: 14; width: 426; text: "What you are authorizing"
            color: Design.text; font.family: Design.fontFamily
            font.pixelSize: 13; font.weight: Font.DemiBold
        }
        Text {
            x: 16; y: 40; width: 426; wrapMode: Text.Wrap
            text: "Send exactly " + root.amount
                + " native USDC from your Arbitrum wallet to the official Hyperliquid Bridge2."
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
        Text {
            x: 16; y: 78; width: 426; wrapMode: Text.Wrap
            text: "Hyperliquid credits native USDC deposits of at least 5 USDC. This transfer cannot be reversed."
            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
    Rectangle {
        y: 142; width: parent.width; height: 172; radius: 12
        color: "#332C261B"; border.width: 1; border.color: "#66D5AA64"
        Text {
            x: 16; y: 14; width: 426; text: "Verified route and maximum fee"
            color: Design.warning; font.family: Design.fontFamily
            font.pixelSize: 13; font.weight: Font.DemiBold
        }
        Text {
            x: 16; y: 42; width: 426; text: "Network · Arbitrum One (42161)"
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
        Text {
            x: 16; y: 64; width: 426; text: "Account · " + root.account
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
        Text {
            x: 16; y: 86; width: 426
            text: "Asset · native USDC · " + root.shortAddress(root.tokenContract)
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
        Text {
            x: 16; y: 108; width: 426
            text: "Destination · Hyperliquid Bridge2 · " + root.shortAddress(root.recipient)
            color: Design.textMuted; font.family: Design.fontFamily; font.pixelSize: 11
        }
        Text {
            x: 16; y: 136; width: 426
            text: "Maximum network fee · " + root.maxTotalFeeWei + " wei"
            color: Design.warning; font.family: Design.fontFamily; font.pixelSize: 11
        }
    }
}
