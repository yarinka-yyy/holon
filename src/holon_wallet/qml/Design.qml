pragma Singleton
import QtQuick

QtObject {
    readonly property color background: "#10181E"
    readonly property color surface: "#131B21"
    readonly property color surfaceCard: "#151E24"
    readonly property color surfaceSecondary: "#182128"
    readonly property color surfaceHover: "#1C272E"
    readonly property color border: "#2A343B"
    readonly property color borderStrong: "#3A454C"
    readonly property color text: "#F2F3F1"
    readonly property color textMuted: "#A5ABB2"
    readonly property color textFaint: "#727C84"
    readonly property color accent: "#84C7BA"
    readonly property color accentHover: "#92D3C6"
    readonly property color accentPressed: "#70B3A7"
    readonly property color accentSoft: "#18332F"
    readonly property color textOnAccent: "#0E1917"
    readonly property color danger: "#E27D7D"
    readonly property color warning: "#D5AA64"
    readonly property string fontFamily: walletFontFamily
    readonly property int fastMotion: 140
    readonly property int normalMotion: 190
    readonly property int pagePadding: 28
    readonly property int cardRadius: 16
    readonly property int controlRadius: 14
}
