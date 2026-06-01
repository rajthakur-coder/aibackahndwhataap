import httpx
from fastapi import HTTPException

from app.config import settings


async def exchange_google_code_for_tokens(code: str, redirect_uri: str):
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        tokens = token_res.json()
        if "access_token" not in tokens:
            raise HTTPException(status_code=400, detail="Failed to retrieve token from Google.")

        user_info_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        return tokens, user_info_res.json()
