from fastapi import APIRouter


router = APIRouter(tags=["system"])


@router.get("/")
def home():
    return {
        "status": "ok",
        "message": "AI WhatsApp Automation Backend Running",
    }


@router.get("/health")
def health():
    return {"status": "healthy"}
