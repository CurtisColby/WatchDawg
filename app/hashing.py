"""
WatchDawg Hashing Utilities.

Provides HMAC-based hashing for fast database lookups on encrypted fields.
The skip list needs to check "is this post ID already skipped?" on every
feed request. Instead of decrypting every row, we store an HMAC hash
alongside the encrypted value and query by hash.

The HMAC uses the app secret key, so hashes can't be reversed or
rainbow-tabled without the key.
"""

import hashlib
import hmac
from app.config import settings


def hmac_hash(value: str) -> str:
    """
    Generate a deterministic HMAC-SHA256 hash of a value.

    Uses APP_SECRET_KEY as the HMAC key so that:
    1. The same input always produces the same hash (for lookups).
    2. The hash can't be reversed without the secret key.

    Args:
        value: The plaintext string to hash (e.g., a source_post_id).

    Returns:
        A 64-character hex string.
    """
    return hmac.new(
        key=settings.app_secret_key.encode(),
        msg=value.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
