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
PERSONALITY_GUIDANCE = {
    "helpful": "Be helpful, accurate, and practical.",
    "sales": "Be consultative and gently sales-oriented without being pushy.",
    "support": "Be patient, reassuring, and support-focused.",
    "luxury": "Be polished, premium, and concise.",
    "playful": "Be warm and lightly playful while staying professional.",
}
TONE_GUIDANCE = {
    "friendly": "Use a friendly conversational tone.",
    "professional": "Use a professional and precise tone.",
    "casual": "Use a simple casual tone.",
    "empathetic": "Use an empathetic, calm tone.",
    "direct": "Use a direct, no-fluff tone.",
}
LENGTH_GUIDANCE = {
    "short": "Keep replies very short, usually 1 sentence.",
    "brief": "Keep replies brief, usually 1-3 short sentences.",
    "detailed": "Give a little more detail when useful, but avoid long paragraphs.",
}
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

    bot_settings = db.execute(select(BotSettings).where(BotSettings.tenant_id == "default")).scalars().first()
    personality = str(getattr(bot_settings, "ai_personality", "helpful") or "helpful").strip().lower()
    tone = str(getattr(bot_settings, "ai_tone", "friendly") or "friendly").strip().lower()
    response_length = str(getattr(bot_settings, "response_length", "brief") or "brief").strip().lower()
    custom_instructions = str(getattr(bot_settings, "custom_instructions", "") or "").strip()

    system_prompt = (
        "You are an advanced WhatsApp AI agent. Reply clearly and briefly. "
        f"Reply in {reply_language}. For neutral greetings like hi or hello, use English unless the user's message has Hindi/Hinglish words. "
        f"{PERSONALITY_GUIDANCE.get(personality, PERSONALITY_GUIDANCE['helpful'])} "
        f"{TONE_GUIDANCE.get(tone, TONE_GUIDANCE['friendly'])} "
        f"{LENGTH_GUIDANCE.get(response_length, LENGTH_GUIDANCE['brief'])} "
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

    if custom_instructions:
        system_prompt += f"\n\nBusiness-specific reply instructions:\n{custom_instructions[:2000]}"

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

