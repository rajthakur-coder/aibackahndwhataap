from pydantic import BaseModel, EmailStr, Field
from typing import Optional


class SignUpRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    credits: int = 0
    verified: bool
    onboarding_completed: bool

    class Config:
        from_attributes = True


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=72)


class OTPVerifyRequest(BaseModel):
    email: EmailStr
    otp: str


class ResendOTPRequest(BaseModel):
    email: EmailStr


class EmailAddress(BaseModel):
    email: EmailStr


class PlatformProfile(BaseModel):
    id: str = "Connected"
    name: str = "Ad Account"
    avatar: Optional[str] = None


class UserConnectionsResponse(BaseModel):
    facebook_connected: bool
    google_connected: bool
    facebook_profile: Optional[PlatformProfile] = None
    google_profile: Optional[PlatformProfile] = None
