import re

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.crm import BotSettings
from app.models.whatsapp import Message


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 45
MAX_REPLY_CHARS = 4096
HINGLISH_TERMS = {
    "aap",
    "abhi",
    "batao",
    "bhejo",
    "chahiye",
    "chaiye",
    "dekhna",
    "dikha",
    "dikhana",
    "dikhao",
    "hai",
    "hain",
    "kaise",
    "karo",
    "kya",
    "mera",
    "mere",
    "mujhe",
    "nahi",
}


def _recent_conversation(db: Session, phone: str) -> list[dict]:
    messages = db.execute(
        select(Message)
        .where(Message.phone == phone)
        .order_by(Message.created_at.desc())
        .limit(8)
    ).scalars().all()

    history = []
    for message in reversed(messages):
        role = "assistant" if message.direction == "outgoing" else "user"
        history.append({"role": role, "content": message.message})
    return history


def generate_ai_reply(
    db: Session,
    phone: str,
    user_message: str,
    agent_context: str = "",
    tool_context: str = "",
) -> str:
    api_key = settings.openrouter_api_key
    model = settings.openrouter_model

    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    context = tool_context.strip()
    reply_language = _reply_language(db, user_message)

    system_prompt = (
        "You are an advanced WhatsApp AI agent. Reply clearly and briefly. "
        f"Reply in {reply_language}. For neutral greetings like hi or hello, use English unless the user's message has Hindi/Hinglish words. "
        "Use only structured database/tool context when it is provided. "
        "Use customer memory and intent context to personalize the response. "
        "Ask for missing details when an action needs more information. "
        "If the answer is not available in the context, say that you do not "
        "have that information instead of inventing details."
    )

    if agent_context:
        system_prompt += f"\n\nCustomer and agent context:\n{agent_context}"

    if context:
        system_prompt += f"\n\nAvailable business context:\n{context}"

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(_recent_conversation(db, phone))
    messages.append({"role": "user", "content": user_message})

    response = requests.post(
        OPENROUTER_CHAT_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": settings.app_url,
            "X-Title": settings.app_name,
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 500,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    data = response.json()
    reply = data["choices"][0]["message"]["content"].strip()
    return reply[:MAX_REPLY_CHARS]


def _reply_language(db: Session, message: str) -> str:
    bot_settings = db.execute(select(BotSettings).where(BotSettings.tenant_id == "default")).scalars().first()
    default_language = str(getattr(bot_settings, "default_language", "auto") or "auto").strip().lower()
    if default_language == "hindi":
        return "Hindi"
    if default_language == "hinglish":
        return "Hinglish"
    if default_language == "english":
        return "English"
    terms = {token.lower() for token in re.findall(r"[a-zA-Z0-9]+", message or "")}
    return "Hinglish" if terms & HINGLISH_TERMS else "English"

