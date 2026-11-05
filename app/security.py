"""Verificación de firma HMAC del webhook."""
from __future__ import annotations

import hashlib
import hmac


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, signature: str | None) -> bool:
    """Si no hay secreto configurado se acepta todo (solo para demo local)."""
    if not secret:
        return True
    if not signature:
        return False
    # compare_digest evita ataques de temporización.
    return hmac.compare_digest(sign(secret, body), signature.strip().lower())
