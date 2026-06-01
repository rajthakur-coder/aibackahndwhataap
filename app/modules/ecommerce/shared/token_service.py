import base64
import hashlib

from app.config import settings

try:
    from cryptography.fernet import Fernet
except ImportError:  # pragma: no cover - dependency is declared for production installs.
    Fernet = None


def encrypt_token(token: str | None) -> str | None:
    if not token:
        return None
    if token.startswith("fernet:"):
        return token
    secret = settings.ECOMMERCE_TOKEN_SECRET
    if not secret or Fernet is None:
        return token
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return "fernet:" + Fernet(key).encrypt(token.encode()).decode()


def decrypt_token(token: str | None) -> str | None:
    if not token:
        return None
    if token.startswith("fernet:"):
        secret = settings.ECOMMERCE_TOKEN_SECRET
        if not secret or Fernet is None:
            return token
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        return Fernet(key).decrypt(token[7:].encode()).decode()
    if not token.startswith("xor:"):
        return token
    secret = settings.ECOMMERCE_TOKEN_SECRET
    if not secret:
        return token
    key = hashlib.sha256(secret.encode()).digest()
    data = base64.urlsafe_b64decode(token[4:].encode())
    return bytes(char ^ key[index % len(key)] for index, char in enumerate(data)).decode()
