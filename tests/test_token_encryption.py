from app.config import settings
from app.modules.ecommerce.shared.token_service import decrypt_token, encrypt_token


def test_token_encryption_round_trip(monkeypatch):
    monkeypatch.setattr(settings, "ECOMMERCE_TOKEN_SECRET", "test-secret")

    encrypted = encrypt_token("shopify-token")

    assert encrypted != "shopify-token"
    assert encrypted.startswith("fernet:")
    assert decrypt_token(encrypted) == "shopify-token"


def test_token_encryption_is_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "ECOMMERCE_TOKEN_SECRET", "test-secret")
    encrypted = encrypt_token("shopify-token")

    encrypted_again = encrypt_token(encrypted)

    assert encrypted_again == encrypted
    assert decrypt_token(encrypted_again) == "shopify-token"


def test_plain_token_survives_without_secret(monkeypatch):
    monkeypatch.setattr(settings, "ECOMMERCE_TOKEN_SECRET", "")

    assert encrypt_token("plain-token") == "plain-token"
    assert decrypt_token("plain-token") == "plain-token"
