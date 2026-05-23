"""
WatchDawg Encryption Module — ChartHound Security Standard.

All sensitive data stored in the database (tokens, API keys, credentials,
skip-list entries) MUST be encrypted at rest using this module.

Principle: Data is encrypted before writing to disk and decrypted ONLY
in memory at the moment of use. The Fernet key never leaves the .env file.

Usage:
    from app.encryption import encrypt_value, decrypt_value

    # Before storing to database:
    encrypted = encrypt_value("my-secret-api-token")

    # When reading from database for use:
    plaintext = decrypt_value(encrypted)
"""

import logging
from cryptography.fernet import Fernet, InvalidToken
from app.config import settings

logger = logging.getLogger(__name__)

# Initialize the Fernet cipher once at module load.
# If the key is invalid, we fail hard — the app should not start
# with broken encryption.
try:
    _fernet = Fernet(settings.fernet_encryption_key.encode())
except Exception as e:
    logger.critical(
        "FATAL: Invalid FERNET_ENCRYPTION_KEY in environment. "
        "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )
    raise SystemExit(
        "Cannot start WatchDawg without a valid FERNET_ENCRYPTION_KEY."
    ) from e


def encrypt_value(plaintext: str) -> str:
    """
    Encrypt a plaintext string for safe storage in the database.

    Args:
        plaintext: The sensitive value to encrypt.

    Returns:
        A URL-safe base64-encoded encrypted string.
    """
    if not plaintext:
        return ""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(encrypted_text: str) -> str:
    """
    Decrypt a value retrieved from the database back to plaintext.

    This should only be called at the moment the value is needed in memory.
    Never log, cache, or persist the returned plaintext.

    Args:
        encrypted_text: The encrypted string from the database.

    Returns:
        The original plaintext string.

    Raises:
        ValueError: If decryption fails (corrupted data or wrong key).
    """
    if not encrypted_text:
        return ""
    try:
        return _fernet.decrypt(encrypted_text.encode()).decode()
    except InvalidToken:
        logger.error(
            "Decryption failed — data may be corrupted or the encryption key has changed."
        )
        raise ValueError(
            "Failed to decrypt value. Check that FERNET_ENCRYPTION_KEY matches "
            "the key used when the data was encrypted."
        )


def rotate_encryption_key(old_key: str, new_key: str, encrypted_text: str) -> str:
    """
    Re-encrypt data from an old key to a new key.

    Useful for key rotation without data loss. Decrypts with the old key,
    re-encrypts with the new key.

    Args:
        old_key: The previous Fernet key (base64 string).
        new_key: The new Fernet key (base64 string).
        encrypted_text: Data encrypted under the old key.

    Returns:
        Data re-encrypted under the new key.
    """
    old_fernet = Fernet(old_key.encode())
    new_fernet = Fernet(new_key.encode())
    plaintext = old_fernet.decrypt(encrypted_text.encode())
    return new_fernet.encrypt(plaintext).decode()
