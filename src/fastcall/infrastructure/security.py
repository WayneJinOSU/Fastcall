import os

from fastcall_core.infrastructure.crypto import PhoneHmac, SecretCodec, decode_keys


def secret_codec_from_env():
    keys = decode_keys(os.getenv("FASTCALL_ENCRYPTION_KEYS_JSON"), "FASTCALL_ENCRYPTION_KEYS_JSON")
    return SecretCodec(keys, os.getenv("FASTCALL_ENCRYPTION_ACTIVE_VERSION", ""))


def phone_hmac_from_env():
    keys = decode_keys(os.getenv("FASTCALL_HMAC_KEYS_JSON"), "FASTCALL_HMAC_KEYS_JSON")
    return PhoneHmac(keys, os.getenv("FASTCALL_HMAC_ACTIVE_VERSION", ""))
