import os

import requests
from sqlalchemy.orm import Session

from models import Message, ScrapedData


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 45
MAX_CONTEXT_CHARS = 6000
MAX_REPLY_CHARS = 4096


def _recent_conversation(db: Session, phone: str) -> list[dict]:
    messages = (
        db.query(Message)
        .filter(Message.phone == phone)
        .order_by(Message.created_at.desc())
        .limit(8)
        .all()
    )

    history = []
    for message in reversed(messages):
        role = "assistant" if message.direction == "outgoing" else "user"
        history.append({"role": role, "content": message.message})
    return history


def _scraped_context(db: Session) -> str:
    rows = (
        db.query(ScrapedData)
        .order_by(ScrapedData.created_at.desc())
        .limit(3)
        .all()
    )

    sections = []
    for row in rows:
        content = row.content[:2000]
        sections.append(f"Source: {row.url}\n{content}")

    return "\n\n".join(sections)[:MAX_CONTEXT_CHARS]


def generate_ai_reply(db: Session, phone: str, user_message: str) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o")

    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    context = _scraped_context(db)
    system_prompt = (
        "You are a helpful WhatsApp assistant. Reply clearly and briefly. "
        "Use the provided website context when it is relevant, and do not "
        "invent details when the answer is not in the context."
    )

    if context:
        system_prompt += f"\n\nWebsite context:\n{context}"

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(_recent_conversation(db, phone))
    messages.append({"role": "user", "content": user_message})

    response = requests.post(
        OPENROUTER_CHAT_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("APP_URL", ""),
            "X-Title": os.getenv("APP_NAME", "AI WhatsApp Automation"),
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
