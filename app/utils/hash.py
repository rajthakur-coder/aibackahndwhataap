from passlib.context import CryptContext


context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_string(password: str) -> str:
    truncated_password = password.encode("utf-8")[:72].decode("utf-8", "ignore")
    return context.hash(truncated_password)


def verify_hash(password: str, hashed_password: str) -> bool:
    truncated_password = password.encode("utf-8")[:72].decode("utf-8", "ignore")
    return context.verify(truncated_password, hashed_password)
