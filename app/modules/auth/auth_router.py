from http import HTTPStatus

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import User
from app.modules.audit import write_async_audit_log
from app.security import get_current_user_token
from app.utils import create_token, get_cookie_options, get_delete_cookie_options
from app.config import settings

from .auth_schema import (
    EmailAddress,
    LoginRequest,
    OTPVerifyRequest,
    ResendOTPRequest,
    ResetPasswordRequest,
    SignUpRequest,
    UserConnectionsResponse,
    UserResponse,
)
from .auth_service import (
    GoogleOAuthCallbackError,
    build_google_oauth_url,
    disconnect_platform,
    forgot_password,
    get_connections,
    process_google_callback,
    resend_otp,
    reset_password,
    serialize_user,
    sign_in,
    sign_up,
    verify_otp,
)


auth_router = APIRouter(prefix="/auth", tags=["Auth"])


@auth_router.post("/sign-up")
async def sign_up_route(
    request: SignUpRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    return await sign_up(request, background_tasks, db, response)


@auth_router.post("/sign-in")
async def sign_in_route(
    request: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    return await sign_in(request, db, response)


@auth_router.get("/me", response_model=UserResponse)
async def me_route(
    current_user=Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="User not found")
    return serialize_user(user)


@auth_router.post("/verify-otp")
async def verify_otp_route(
    request: OTPVerifyRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    return await verify_otp(request, db, response)


@auth_router.post("/resend-otp")
async def resend_otp_route(
    request: ResendOTPRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    return await resend_otp(request, background_tasks, db)


@auth_router.post("/forgot-password")
async def forgot_password_route(
    body: EmailAddress,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    return await forgot_password(body.email, background_tasks, db)


@auth_router.post("/reset-password")
async def reset_password_route(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    return await reset_password(body, db)


@auth_router.post("/sign-out")
async def sign_out_route(
    response: Response,
    current_user=Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db),
):
    await write_async_audit_log(
        db,
        action="auth.sign_out",
        tenant_id=current_user.tenant_id,
        user_id=str(current_user.id),
        entity_type="user",
        entity_id=current_user.id,
        commit=True,
    )
    response.delete_cookie(**get_delete_cookie_options("access_token"))
    return {"message": "User logged out successfully"}


@auth_router.get("/connections", response_model=UserConnectionsResponse)
async def get_my_connections_route(
    current_user=Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db),
):
    return await get_connections(current_user, db)


@auth_router.delete("/connections/{platform}")
async def disconnect_platform_route(
    platform: str,
    current_user=Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db),
):
    return await disconnect_platform(platform, current_user, db)


@auth_router.get("/google/login")
async def google_signin():
    return RedirectResponse(url=build_google_oauth_url())


@auth_router.get("/google/callback")
async def google_callback(request: Request, db: AsyncSession = Depends(get_db)):
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    alignads_app_url = settings.ALIGNADS_APP_URL or settings.ALIGNAUTH_APP_URL

    if error:
        return RedirectResponse(f"{alignads_app_url}/sign-in?error=access_denied")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    try:
        user = await process_google_callback(db=db, code=code)
    except GoogleOAuthCallbackError as exc:
        return RedirectResponse(f"{alignads_app_url}/sign-in?error={exc.code}")

    access_token = create_token({"id": str(user.id)})
    final_res = RedirectResponse(
        url=f"{alignads_app_url}/on-boarding"
        if not user.onboarding_completed
        else f"{alignads_app_url}/dashboard"
    )
    final_res.set_cookie(
        **get_cookie_options(
            key="access_token",
            value=access_token,
            max_age=30 * 24 * 60 * 60,
        )
    )
    return final_res
