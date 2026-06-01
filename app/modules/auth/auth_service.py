import asyncio
import secrets
import time
import urllib.parse
from http import HTTPStatus

from fastapi import BackgroundTasks, HTTPException, Response
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mail_templates import get_forgot_password_template, get_otp_email_template
from app.models import User
from app.models.integration import Integration
from app.models.integration.constants import IntegrationProvider, IntegrationStatus
from app.modules.auth.auth_schema import (
    LoginRequest,
    OTPVerifyRequest,
    PlatformProfile,
    ResendOTPRequest,
    ResetPasswordRequest,
    SignUpRequest,
    UserConnectionsResponse,
)
from app.modules.audit import write_async_audit_log
from app.modules.ecommerce.shared.token_service import encrypt_token
from app.shared.exchange_google_code_for_tokens import exchange_google_code_for_tokens
from app.shared.redis import get_redis
from app.shared.tenant import normalize_tenant_id, reset_current_tenant_id, set_current_tenant_id
from app.utils import create_token, decode_token, get_cookie_options, hash_string, send_email, verify_hash


OTP_TTL_SECONDS = 60 * 60
_local_otp_store: dict[str, tuple[str, float]] = {}


class GoogleOAuthCallbackError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _tenant_id_for_user(user: User) -> str:
    return normalize_tenant_id(str(user.id))


async def _public_user_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email))


async def _public_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    return await db.get(User, user_id)


async def _scope_to_user(db: AsyncSession, user: User):
    return set_current_tenant_id(_tenant_id_for_user(user))


def _auth_payload(user: User) -> dict:
    return {
        "id": str(user.id),
    }


def _set_auth_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        **get_cookie_options(
            key="access_token",
            value=create_token(_auth_payload(user)),
            max_age=30 * 24 * 60 * 60,
        )
    )


def _otp_key(email: str) -> str:
    return f"otp:{email.lower()}"


def _generate_otp() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(6))


async def _store_otp(email: str, otp: str) -> None:
    key = _otp_key(email)
    try:
        redis = await get_redis()
        await redis.set(key, otp, ex=OTP_TTL_SECONDS)
    except RedisError:
        _local_otp_store[key] = (otp, time.time() + OTP_TTL_SECONDS)


async def _read_otp(email: str) -> str | None:
    key = _otp_key(email)
    try:
        redis = await get_redis()
        return await redis.get(key)
    except RedisError:
        value = _local_otp_store.get(key)
        if not value:
            return None
        otp, expires_at = value
        if expires_at < time.time():
            _local_otp_store.pop(key, None)
            return None
        return otp


async def _delete_otp(email: str) -> None:
    key = _otp_key(email)
    try:
        redis = await get_redis()
        await redis.delete(key)
    except RedisError:
        _local_otp_store.pop(key, None)


async def _send_otp_email(user: User, background_tasks: BackgroundTasks) -> None:
    otp = _generate_otp()
    await _store_otp(user.email, otp)
    send_email(
        subject="Your Align Labs Verification Code",
        recipients=[user.email],
        body=get_otp_email_template(user.name, otp),
        background_tasks=background_tasks,
    )


def serialize_user(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "credits": user.credits,
        "verified": user.verified,
        "onboarding_completed": user.onboarding_completed,
    }


async def sign_up(
    request: SignUpRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
    response: Response,
) -> dict:
    existing_user = await _public_user_by_email(db, request.email)
    if existing_user is not None:
        raise HTTPException(status_code=HTTPStatus.CONFLICT, detail="User already exists")

    user = User(
        name=request.name,
        email=request.email,
        password=hash_string(request.password),
        verified=False,
        onboarding_completed=False,
    )
    tenant_token = set_current_tenant_id(None)
    try:
        db.add(user)
        await db.commit()
        await db.refresh(user)
        reset_current_tenant_id(tenant_token)
        tenant_token = set_current_tenant_id(_tenant_id_for_user(user))
        await write_async_audit_log(
            db,
            action="auth.sign_up",
            tenant_id=_tenant_id_for_user(user),
            user_id=str(user.id),
            entity_type="user",
            entity_id=user.id,
            metadata={"email": user.email, "verified": user.verified},
            commit=True,
        )
        await _send_otp_email(user, background_tasks)
    finally:
        reset_current_tenant_id(tenant_token)

    return {
        "message": "User created. Please check your email for the OTP.",
        "email": user.email,
    }


async def verify_otp(request: OTPVerifyRequest, db: AsyncSession, response: Response) -> dict:
    stored_otp = await _read_otp(request.email)
    if not stored_otp:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="OTP expired or invalid.")

    if stored_otp != request.otp:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="Incorrect OTP.")

    user = await _public_user_by_email(db, request.email)
    if not user:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="User not found.")

    tenant_token = await _scope_to_user(db, user)
    try:
        user.verified = True
        await db.commit()
        await db.refresh(user)
        await _delete_otp(user.email)
        _set_auth_cookie(response, user)
        await write_async_audit_log(
            db,
            action="auth.verify_otp",
            tenant_id=_tenant_id_for_user(user),
            user_id=str(user.id),
            entity_type="user",
            entity_id=user.id,
            metadata={"email": user.email},
            commit=True,
        )
    finally:
        reset_current_tenant_id(tenant_token)

    return {"message": "Email verified successfully.", **serialize_user(user)}


async def resend_otp(
    request: ResendOTPRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
) -> dict:
    user = await _public_user_by_email(db, request.email)
    if not user:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="User not found.")

    if user.verified:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="User already verified.")

    tenant_token = await _scope_to_user(db, user)
    try:
        await _send_otp_email(user, background_tasks)
        await write_async_audit_log(
            db,
            action="auth.resend_otp",
            tenant_id=_tenant_id_for_user(user),
            user_id=str(user.id),
            entity_type="user",
            entity_id=user.id,
            metadata={"email": user.email},
            commit=True,
        )
    finally:
        reset_current_tenant_id(tenant_token)
    return {"message": "OTP sent to your email."}


async def sign_in(request: LoginRequest, db: AsyncSession, response: Response) -> dict:
    user = await _public_user_by_email(db, request.email)
    if user is None:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="Invalid email or password")

    is_valid_password = await asyncio.to_thread(verify_hash, request.password, user.password)
    if not is_valid_password:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="Invalid email or password")

    if not user.verified:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="Please verify your email before signing in",
        )

    tenant_token = await _scope_to_user(db, user)
    try:
        _set_auth_cookie(response, user)
        await write_async_audit_log(
            db,
            action="auth.sign_in",
            tenant_id=_tenant_id_for_user(user),
            user_id=str(user.id),
            entity_type="user",
            entity_id=user.id,
            metadata={"email": user.email},
            commit=True,
        )
    finally:
        reset_current_tenant_id(tenant_token)

    return {"message": "User logged in", **serialize_user(user)}


async def reset_password(body: ResetPasswordRequest, db: AsyncSession) -> dict:
    payload = decode_token(body.token)
    user_id = payload.get("id") if payload else None
    if user_id is None:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="Invalid token")

    user = await _public_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="User not found")

    tenant_token = await _scope_to_user(db, user)
    try:
        user.password = hash_string(body.new_password)
        await write_async_audit_log(
            db,
            action="auth.reset_password",
            tenant_id=_tenant_id_for_user(user),
            user_id=str(user.id),
            entity_type="user",
            entity_id=user.id,
            metadata={"email": user.email},
        )
        await db.commit()
    finally:
        reset_current_tenant_id(tenant_token)
    return {"message": "Password reset successfully"}


async def forgot_password(email: str, background_tasks: BackgroundTasks, db: AsyncSession) -> dict:
    user = await _public_user_by_email(db, email)
    if user is None:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="User not found")

    tenant_token = await _scope_to_user(db, user)
    try:
        token = create_token({"id": str(user.id)})
        send_email(
            subject="Reset your password for Align Labs",
            recipients=[email],
            body=get_forgot_password_template(user.name, token),
            background_tasks=background_tasks,
        )
        await write_async_audit_log(
            db,
            action="auth.forgot_password",
            tenant_id=_tenant_id_for_user(user),
            user_id=str(user.id),
            entity_type="user",
            entity_id=user.id,
            metadata={"email": user.email},
            commit=True,
        )
    finally:
        reset_current_tenant_id(tenant_token)
    return {"message": "Password reset email sent"}


async def get_connections(current_user, db: AsyncSession):
    rows = (
        await db.execute(
            select(Integration).where(
                Integration.user_id == current_user.id,
                Integration.status == IntegrationStatus.CONNECTED,
            )
        )
    ).scalars().all()

    fb_integration = next((row for row in rows if row.provider == IntegrationProvider.META_ADS), None)
    google_integration = next((row for row in rows if row.provider == IntegrationProvider.GOOGLE_ADS), None)

    return UserConnectionsResponse(
        facebook_connected=bool(fb_integration),
        google_connected=bool(google_integration),
        facebook_profile=PlatformProfile(name=fb_integration.display_name or "Facebook Account") if fb_integration else None,
        google_profile=PlatformProfile(name=google_integration.display_name or "Google Account") if google_integration else None,
    )


async def disconnect_platform(platform: str, current_user, db: AsyncSession):
    if platform == "facebook":
        provider = IntegrationProvider.META_ADS
    elif platform == "google":
        provider = IntegrationProvider.GOOGLE_ADS
    else:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="Invalid platform")

    rows = (
        await db.execute(
            select(Integration).where(
                Integration.user_id == current_user.id,
                Integration.provider == provider,
            )
        )
    ).scalars().all()
    for row in rows:
        row.status = IntegrationStatus.DISCONNECTED
    await db.commit()
    return {"status": "success", "message": f"{platform} disconnected successfully."}


def build_google_oauth_url() -> str:
    redirect_uri = _google_redirect_uri()
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
        "state": "temp_login_flow",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


async def process_google_callback(db: AsyncSession, code: str) -> User:
    try:
        tokens, user_profile = await exchange_google_code_for_tokens(code, _google_redirect_uri())
    except Exception as exc:
        raise GoogleOAuthCallbackError("token_failed") from exc

    email = str(user_profile.get("email") or "").strip().lower()
    if not email:
        raise GoogleOAuthCallbackError("email_missing")

    user = await _public_user_by_email(db, email)
    if not user:
        user = User(
            name=str(user_profile.get("name") or email.split("@", 1)[0]),
            email=email,
            password=hash_string(secrets.token_urlsafe(32)[:32]),
            verified=True,
            onboarding_completed=False,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    user.verified = True
    await _upsert_google_connection(db, user, user_profile, tokens)
    await db.commit()
    await db.refresh(user)
    return user


def _google_redirect_uri() -> str:
    base_url = settings.API_BASE_URL or settings.APP_URL
    return f"{base_url.rstrip('/')}/auth/google/callback"


async def _upsert_google_connection(db: AsyncSession, user: User, user_profile: dict, tokens: dict) -> Integration:
    provider_account_id = str(user_profile.get("id") or user_profile.get("email") or user.email)
    row = await db.scalar(
        select(Integration).where(
            Integration.user_id == user.id,
            Integration.provider == IntegrationProvider.GOOGLE_ADS,
            Integration.provider_account_id == provider_account_id,
        )
    )
    if not row:
        row = Integration(
            user_id=user.id,
            tenant_id=_tenant_id_for_user(user),
            provider=IntegrationProvider.GOOGLE_ADS,
            provider_account_id=provider_account_id,
        )
        db.add(row)

    row.status = IntegrationStatus.CONNECTED
    row.display_name = str(user_profile.get("name") or user_profile.get("email") or "Google Account")
    row.scopes = "[]"
    row.access_token = encrypt_token(tokens.get("access_token"))
    row.refresh_token = encrypt_token(tokens.get("refresh_token"))
    return row
