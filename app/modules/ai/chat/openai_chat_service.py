import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import BotSettings
from app.models.whatsapp import Message
from app.modules.compliance.pii import redact_pii
from app.modules.headless.llm_provider import chat_completion
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


MAX_REPLY_CHARS = 4096
MAX_REPLY_TOKENS = 320
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


def _recent_conversation(db: Session, phone: str, tenant_id: str = DEFAULT_TENANT_ID) -> list[dict]:
    tenant_id = normalize_tenant_id(tenant_id)
    messages = db.execute(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.phone == phone)
        .order_by(Message.created_at.desc())
        .limit(8)
    ).scalars().all()

    history = []
    for message in reversed(messages):
        role = "assistant" if message.direction == "outgoing" else "user"
        history.append({"role": role, "content": redact_pii(message.message)})
    return history


def generate_ai_reply(
    db: Session,
    phone: str,
    user_message: str,
    agent_context: str = "",
    tool_context: str = "",
    tenant_id: str = DEFAULT_TENANT_ID,
) -> str:
    context = redact_pii(tool_context.strip())
    tenant_id = normalize_tenant_id(tenant_id)
    reply_language = _reply_language(db, user_message, tenant_id=tenant_id)

    bot_settings = db.execute(select(BotSettings).where(BotSettings.tenant_id == tenant_id)).scalars().first()
    personality = str(getattr(bot_settings, "ai_personality", "helpful") or "helpful").strip().lower()
    tone = str(getattr(bot_settings, "ai_tone", "friendly") or "friendly").strip().lower()
    response_length = str(getattr(bot_settings, "response_length", "brief") or "brief").strip().lower()
    custom_instructions = str(getattr(bot_settings, "custom_instructions", "") or "").strip()
    brand_prompt = str(getattr(bot_settings, "brand_prompt", "") or "").strip()

    if brand_prompt:
        system_prompt = brand_prompt
        system_prompt = system_prompt.replace("{reply_language}", reply_language)
    else:
        system_prompt = (
            "You are an advanced WhatsApp AI agent. Reply clearly and briefly. "
            f"Reply in {reply_language}. For neutral greetings like hi or hello, use English unless the user's message has Hindi/Hinglish words. "
            f"{PERSONALITY_GUIDANCE.get(personality, PERSONALITY_GUIDANCE['helpful'])} "
            f"{TONE_GUIDANCE.get(tone, TONE_GUIDANCE['friendly'])} "
            f"{LENGTH_GUIDANCE.get(response_length, LENGTH_GUIDANCE['brief'])} "
            "Use only structured database/tool context when it is provided. "
            "Never invent product specs, stock, prices, discounts, return eligibility, warranty coverage, or delivery dates. "
            "For order, return, catalog, and policy facts, rely only on the tool context. "
            "Destructive actions require a confirmation result from the tool layer before you imply anything changed. "
            "Use customer memory and intent context to personalize the response. "
            "Ask for missing details when an action needs more information. "
            "If the answer is not available in the context, say that you do not "
            "have that information instead of inventing details. "
            "Keep WhatsApp replies to five short lines or fewer."
        )

    if agent_context:
        system_prompt += f"\n\nCustomer and agent context:\n{redact_pii(agent_context)}"

    if context:
        system_prompt += f"\n\nAvailable business context:\n{context}"

    if not brand_prompt and custom_instructions:
        system_prompt += f"\n\nBusiness-specific reply instructions:\n{custom_instructions[:2000]}"

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(_recent_conversation(db, phone, tenant_id=tenant_id))
    messages.append({"role": "user", "content": redact_pii(user_message)})

    response = chat_completion(
        db,
        tenant_id=tenant_id,
        messages=messages,
        purpose="reply",
        temperature=0.4,
        max_tokens=MAX_REPLY_TOKENS,
    )
    reply = response.content.strip()
    return reply[:MAX_REPLY_CHARS]


def _reply_language(db: Session, message: str, tenant_id: str = DEFAULT_TENANT_ID) -> str:
    tenant_id = normalize_tenant_id(tenant_id)
    bot_settings = db.execute(select(BotSettings).where(BotSettings.tenant_id == tenant_id)).scalars().first()
    default_language = str(getattr(bot_settings, "default_language", "auto") or "auto").strip().lower()
    if default_language == "hindi":
        return "Hindi"
    if default_language == "hinglish":
        return "Hinglish"
    if default_language == "english":
        return "English"
    terms = {token.lower() for token in re.findall(r"[a-zA-Z0-9]+", message or "")}
    return "Hinglish" if terms & HINGLISH_TERMS else "English"

