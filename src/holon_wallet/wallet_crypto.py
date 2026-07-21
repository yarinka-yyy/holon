"""Local-only mnemonic and EVM private-key handling."""

from __future__ import annotations

from dataclasses import dataclass

from bip_utils import (
    Bip39Languages,
    Bip39MnemonicGenerator,
    Bip39MnemonicValidator,
    Bip39SeedGenerator,
    Bip39WordsNum,
    Bip44,
    Bip44Changes,
    Bip44Coins,
)
from eth_keys import keys

DERIVATION_PATH = "m/44'/60'/0'/0/0"
MNEMONIC_PROFILE = "mnemonic"
RAW_KEY_PROFILE = "raw_private_key"


class InvalidSecretError(ValueError):
    """A secret did not match a supported local import format."""


@dataclass(frozen=True, slots=True, repr=False)
class SecretMaterial:
    profile_type: str
    value: str
    address: str
    derivation_path: str | None


def generate_mnemonic() -> SecretMaterial:
    mnemonic = str(
        Bip39MnemonicGenerator(Bip39Languages.ENGLISH).FromWordsNumber(
            Bip39WordsNum.WORDS_NUM_12,
        ),
    )
    return import_mnemonic(mnemonic)


def import_mnemonic(value: str) -> SecretMaterial:
    normalized = " ".join(value.strip().lower().split())
    words = normalized.split()
    if len(words) not in {12, 24}:
        raise InvalidSecretError("Seed phrase must contain 12 or 24 words")
    if not Bip39MnemonicValidator(Bip39Languages.ENGLISH).IsValid(normalized):
        raise InvalidSecretError("Seed phrase is invalid")
    seed = Bip39SeedGenerator(normalized, Bip39Languages.ENGLISH).Generate()
    private_bytes = (
        Bip44.FromSeed(seed, Bip44Coins.ETHEREUM)
        .Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT)
        .AddressIndex(0).PrivateKey().Raw().ToBytes()
    )
    return SecretMaterial(
        MNEMONIC_PROFILE,
        normalized,
        _address_from_private_bytes(private_bytes),
        DERIVATION_PATH,
    )


def import_private_key(value: str) -> SecretMaterial:
    normalized = value.strip()
    if normalized[:2].lower() == "0x":
        normalized = normalized[2:]
    if len(normalized) != 64:
        raise InvalidSecretError("Private key must contain 64 hexadecimal characters")
    try:
        private_bytes = bytes.fromhex(normalized)
        address = _address_from_private_bytes(private_bytes)
    except (ValueError, TypeError):
        raise InvalidSecretError("Private key is invalid") from None
    return SecretMaterial(RAW_KEY_PROFILE, normalized.lower(), address, None)


def rederive(material: SecretMaterial) -> str:
    if material.profile_type == MNEMONIC_PROFILE:
        return import_mnemonic(material.value).address
    if material.profile_type == RAW_KEY_PROFILE:
        return import_private_key(material.value).address
    raise InvalidSecretError("Profile type is unsupported")


def private_key_bytes(material: SecretMaterial) -> bytes:
    """Derive one transient signing key from supported local secret material."""
    if material.profile_type == MNEMONIC_PROFILE:
        normalized = import_mnemonic(material.value)
        seed = Bip39SeedGenerator(
            normalized.value, Bip39Languages.ENGLISH,
        ).Generate()
        return (
            Bip44.FromSeed(seed, Bip44Coins.ETHEREUM)
            .Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT)
            .AddressIndex(0).PrivateKey().Raw().ToBytes()
        )
    if material.profile_type == RAW_KEY_PROFILE:
        return bytes.fromhex(import_private_key(material.value).value)
    raise InvalidSecretError("Profile type is unsupported")


def _address_from_private_bytes(value: bytes) -> str:
    try:
        return keys.PrivateKey(value).public_key.to_checksum_address()
    except Exception:
        raise InvalidSecretError("Private key is invalid") from None
