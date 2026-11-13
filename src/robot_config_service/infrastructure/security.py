import base64
import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from robot_config_service.domain.errors import RobotConfigError


def _decode_keys(value, name):
    try:
        raw = json.loads(value or "{}")
        keys = {
            version: base64.b64decode(encoded, validate=True)
            for version, encoded in raw.items()
        }
    except (ValueError, TypeError, AttributeError) as error:
        raise RuntimeError(f"{name} must be a JSON object of base64 keys") from error
    if not keys or any(len(key) != 32 for key in keys.values()):
        raise RuntimeError(f"{name} must contain at least one 32-byte key")
    return keys


class SecretCodec:
    def __init__(self, keys, active_version):
        self.keys = dict(keys)
        self.active_version = active_version
        if active_version not in self.keys:
            raise RuntimeError("Active encryption key version is not configured")

    @classmethod
    def from_env(cls):
        keys = _decode_keys(os.getenv("FASTCALL_ENCRYPTION_KEYS_JSON"), "FASTCALL_ENCRYPTION_KEYS_JSON")
        return cls(keys, os.getenv("FASTCALL_ENCRYPTION_ACTIVE_VERSION", ""))

    def encrypt(self, value):
        if isinstance(value, str):
            value = value.encode("utf-8")
        nonce = os.urandom(12)
        return nonce + AESGCM(self.keys[self.active_version]).encrypt(nonce, value, None)

    def encrypt_json(self, value):
        return self.encrypt(json.dumps(value, separators=(",", ":")))

    def decrypt(self, value, key_version):
        if key_version not in self.keys:
            raise RobotConfigError("ENCRYPTION_KEY_UNAVAILABLE", "Encryption key version is unavailable")
        if not isinstance(value, bytes) or len(value) < 29:
            raise RobotConfigError("DECRYPTION_FAILED", "Encrypted value is invalid")
        try:
            plaintext = AESGCM(self.keys[key_version]).decrypt(value[:12], value[12:], None)
            return plaintext.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise RobotConfigError("DECRYPTION_FAILED", "Unable to decrypt value") from error

    def decrypt_json(self, value, key_version):
        try:
            result = json.loads(self.decrypt(value, key_version))
        except json.JSONDecodeError as error:
            raise RobotConfigError("DECRYPTION_FAILED", "Encrypted JSON value is invalid") from error
        if not isinstance(result, dict):
            raise RobotConfigError("DECRYPTION_FAILED", "Encrypted variables must be an object")
        return result


class PhoneHmac:
    def __init__(self, keys, active_version):
        self.keys = dict(keys)
        self.active_version = active_version
        if active_version not in self.keys:
            raise RuntimeError("Active HMAC key version is not configured")

    @classmethod
    def from_env(cls):
        keys = _decode_keys(os.getenv("FASTCALL_HMAC_KEYS_JSON"), "FASTCALL_HMAC_KEYS_JSON")
        return cls(keys, os.getenv("FASTCALL_HMAC_ACTIVE_VERSION", ""))

    def sign(self, phone, version=None):
        version = version or self.active_version
        if version not in self.keys:
            raise RobotConfigError("HMAC_KEYS_UNAVAILABLE", "HMAC key version is unavailable")
        return hmac.new(self.keys[version], phone.encode("ascii"), hashlib.sha256).hexdigest()

    def all_versions(self, phone):
        return [(self.sign(phone, version), version) for version in sorted(self.keys)]
