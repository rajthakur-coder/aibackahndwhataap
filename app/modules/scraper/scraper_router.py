import logging

from fastapi import APIRouter, Depends, HTTPException

from app.security import get_current_user_token
from .scraper_schema import ScraperInput, ScraperResponse
from .scraper_service import run_brand_scraper


logger = logging.getLogger(__name__)

scraper_router = APIRouter(
    prefix="/scrape",
    tags=["scraper"],
    dependencies=[Depends(get_current_user_token)],
)


@scraper_router.post("", response_model=ScraperResponse)
async def scrape_brand(payload: ScraperInput):
    try:
        return await run_brand_scraper(payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Scrape error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@scraper_router.post("/partner", response_model=ScraperResponse)
async def scrape_brand_partner(payload: ScraperInput):
    return await scrape_brand(payload)
