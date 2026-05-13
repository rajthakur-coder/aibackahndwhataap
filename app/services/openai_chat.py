import os

import requests
from sqlalchemy.orm import Session

from app.models.entities import Message, ScrapedData
from app.services.rag import retrieve_relevant_context


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 45
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


def _latest_scraped_context(db: Session) -> str:
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

    return "\n\n".join(sections)[:6000]


def generate_ai_reply(
    db: Session,
    phone: str,
    user_message: str,
    agent_context: str = "",
    tool_context: str = "",
    use_rag_fallback: bool = True,
) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o")

    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    context = tool_context.strip()
    rag_context = ""
    if use_rag_fallback and not context:
        rag_context = retrieve_relevant_context(db, user_message)
        context = rag_context
    elif use_rag_fallback:
        rag_context = retrieve_relevant_context(db, user_message)
        if rag_context:
            context = f"{context}\n\nFallback knowledge context:\n{rag_context}".strip()

    if not context:
        context = _latest_scraped_context(db)

    system_prompt = (
        "You are an advanced WhatsApp AI agent. Reply clearly and briefly. "
        "Use structured database/tool context first when it is provided. "
        "Use fallback knowledge context only when the database/tool context is not enough. "
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

