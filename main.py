import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from database import engine, get_db
from models import Base, Message, ScrapedData
from openai_service import generate_ai_reply
from scraper import scrape_website
from whatsapp_service import send_whatsapp_message

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI WhatsApp Automation API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SendMessageRequest(PydanticBaseModel):
    phone: str
    message: str


class ScrapeRequest(PydanticBaseModel):
    url: str


def save_message(db: Session, phone: str, message: str, direction: str) -> Message:
    row = Message(phone=phone, message=message, direction=direction)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def parse_whatsapp_messages(payload: dict) -> list[dict]:
    parsed_messages = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                text = message.get("text", {}).get("body")
                phone = message.get("from")

                if phone and text:
                    parsed_messages.append({"phone": phone, "text": text})

    return parsed_messages


@app.get("/")
def home():
    return {
        "status": "ok",
        "message": "AI WhatsApp Automation Backend Running",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return """
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Scrape Website</title>
        <style>
          body { font-family: Arial, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; }
          form { display: flex; gap: 8px; }
          input { flex: 1; padding: 10px; }
          button { padding: 10px 14px; cursor: pointer; }
          pre { white-space: pre-wrap; background: #f4f4f4; padding: 12px; }
        </style>
      </head>
      <body>
        <h1>Scrape Website</h1>
        <form id="scrape-form">
          <input id="url" type="url" placeholder="https://example.com" required />
          <button type="submit">Scrape Website</button>
        </form>
        <pre id="result"></pre>
        <script>
          document.getElementById("scrape-form").addEventListener("submit", async (event) => {
            event.preventDefault();
            const result = document.getElementById("result");
            result.textContent = "Scraping...";
            const response = await fetch("/scrape", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ url: document.getElementById("url").value })
            });
            result.textContent = JSON.stringify(await response.json(), null, 2);
          });
        </script>
      </body>
    </html>
    """


@app.post("/send-message")
async def send_message(
    data: SendMessageRequest,
    db: Session = Depends(get_db),
):
    try:
        response = await run_in_threadpool(
            send_whatsapp_message,
            data.phone,
            data.message,
        )
        save_message(db, data.phone, data.message, "outgoing")
        return {"status": "sent", "whatsapp": response}
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    verify_token = os.getenv("VERIFY_TOKEN")

    if mode == "subscribe" and token == verify_token and challenge:
        return PlainTextResponse(content=challenge)

    return PlainTextResponse(content="Verification failed", status_code=403)


@app.post("/webhook")
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid_json"}

    try:
        incoming_messages = parse_whatsapp_messages(body)

        for incoming in incoming_messages:
            phone = incoming["phone"]
            text = incoming["text"]

            save_message(db, phone, text, "incoming")

            ai_reply = generate_ai_reply(db, phone, text)
            await run_in_threadpool(send_whatsapp_message, phone, ai_reply)
            save_message(db, phone, ai_reply, "outgoing")

        return {"status": "ok", "processed": len(incoming_messages)}

    except Exception as exc:
        print("WEBHOOK ERROR:", exc)
        return {"status": "ok", "processed": 0}


@app.post("/scrape")
async def scrape(data: ScrapeRequest, db: Session = Depends(get_db)):
    try:
        content = await run_in_threadpool(scrape_website, data.url)
        row = ScrapedData(url=data.url, content=content)
        db.add(row)
        db.commit()
        db.refresh(row)

        return {
            "status": "success",
            "id": row.id,
            "url": row.url,
            "content_length": len(row.content),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Scrape failed: {exc}") from exc


@app.get("/scraped-data")
def list_scraped_data(db: Session = Depends(get_db)):
    rows = db.query(ScrapedData).order_by(ScrapedData.created_at.desc()).all()
    return [
        {
            "id": row.id,
            "url": row.url,
            "content_length": len(row.content),
            "created_at": str(row.created_at),
        }
        for row in rows
    ]

@app.get("/conversations")
def get_conversations(db: Session = Depends(get_db)):
    conversations = db.query(Message.phone).distinct().all()
    return [{"phone": conversation[0]} for conversation in conversations]


@app.get("/messages/{phone}")
def get_messages(phone: str, db: Session = Depends(get_db)):
    messages = (
        db.query(Message)
        .filter(Message.phone == phone)
        .order_by(Message.created_at.asc())
        .all()
    )

    return [
        {
            "id": message.id,
            "phone": message.phone,
            "message": message.message,
            "direction": message.direction,
            "created_at": str(message.created_at),
        }
        for message in messages
    ]

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
