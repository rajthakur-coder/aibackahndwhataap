import asyncio
import logging
from typing import Any

import anyio

from .engine.perplexity import get_brand_intelligence
from .engine.scraper import scrape_brand_fields_only
from .scraper_schema import (
    ScraperCompetitorOut,
    ScraperInput,
    ScraperResponse,
    ScraperResultOut,
    ScraperSocialOut,
)


logger = logging.getLogger(__name__)


def _extract_social_fields(social: Any) -> tuple[str | None, str | None]:
    if isinstance(social, str):
        value = social.strip()
        return (value if value else None, None)

    if not isinstance(social, dict):
        return (None, None)

    raw_url = social.get("url") or social.get("link") or social.get("href")
    raw_type = social.get("type") or social.get("platform") or social.get("name")
    return (
        raw_url.strip() if isinstance(raw_url, str) and raw_url.strip() else None,
        raw_type.strip() if isinstance(raw_type, str) and raw_type.strip() else None,
    )


def _has_any_social_url(items: object) -> bool:
    if not isinstance(items, list):
        return False
    return any(_extract_social_fields(item)[0] for item in items)


def _normalize_url(value: str) -> str:
    return value.strip().rstrip("/")


def _normalize_social_type(raw_type: str | None, raw_url: str | None) -> str | None:
    raw = (raw_type or raw_url or "").lower()
    if "linkedin" in raw:
        return "linkedin"
    if "instagram" in raw:
        return "instagram"
    if "facebook" in raw:
        return "facebook"
    if "youtube" in raw or "youtu.be" in raw:
        return "youtube"
    if "tiktok" in raw:
        return "tiktok"
    if "twitter" in raw or "x.com" in raw:
        return "twitter"
    return raw_type.strip().lower() if raw_type else None


def _normalize_socials(assets: dict, intelligence: dict) -> list[ScraperSocialOut]:
    socials_visual = assets.get("socials") if isinstance(assets.get("socials"), list) else []
    socials_intel = intelligence.get("socials") if isinstance(intelligence.get("socials"), list) else []
    picked_socials = [*socials_visual, *socials_intel]

    seen: set[str] = set()
    output: list[ScraperSocialOut] = []
    for social in picked_socials:
        raw_url, raw_type = _extract_social_fields(social)
        if not raw_url:
            continue
        url = _normalize_url(raw_url)
        social_type = _normalize_social_type(raw_type, url)
        if not social_type or url in seen:
            continue
        seen.add(url)
        output.append(ScraperSocialOut(type=social_type, url=url))
    return output


def _normalize_competitors(intelligence: dict) -> list[ScraperCompetitorOut]:
    competitors = intelligence.get("competitors")
    competitors = competitors if isinstance(competitors, list) else []

    output: list[ScraperCompetitorOut] = []
    for competitor in competitors:
        if not isinstance(competitor, dict):
            continue
        name = str(competitor.get("name") or "").strip()
        if not name:
            continue
        output.append(
            ScraperCompetitorOut(
                name=name,
                url=str(competitor.get("url") or "").strip(),
            )
        )
    return output


async def run_brand_scraper(payload: ScraperInput) -> ScraperResponse:
    target_url = str(payload.website_link)
    logger.info("Starting brand scrape for: %s", target_url)

    try:
        visual_result, intelligence_result = await asyncio.gather(
            anyio.to_thread.run_sync(scrape_brand_fields_only, target_url),
            anyio.to_thread.run_sync(get_brand_intelligence, target_url),
            return_exceptions=True,
        )
    except Exception as exc:
        logger.error("Scraper gather failed: %s", exc, exc_info=True)
        visual_result, intelligence_result = {}, {}

    assets = {} if isinstance(visual_result, Exception) else (visual_result or {})
    intelligence = (
        {} if isinstance(intelligence_result, Exception) else (intelligence_result or {})
    )

    if isinstance(visual_result, Exception):
        logger.error("Visual scraper failed: %s", visual_result, exc_info=True)
    if isinstance(intelligence_result, Exception):
        logger.error("Perplexity scraper failed: %s", intelligence_result, exc_info=True)

    return ScraperResponse(
        status="success",
        data=ScraperResultOut(
            company_name=intelligence.get("company_name") or assets.get("company_name"),
            industry=intelligence.get("industry"),
            about_company=intelligence.get("about_company") or "",
            website_link=target_url,
            logo=assets.get("logo"),
            color_palette=assets.get("color_palette") or [],
            fonts=assets.get("fonts") or [],
            target_demographics=intelligence.get("target_demographics"),
            policies=_clean_text(intelligence.get("policies")),
            faqs=_clean_text(intelligence.get("faqs")),
            socials=_normalize_socials(assets, intelligence),
            competitors=_normalize_competitors(intelligence),
            page_images=assets.get("page_images") or [],
        ),
    )


def _clean_text(value: object) -> str | None:
    if isinstance(value, list):
        value = "\n".join(str(item).strip() for item in value if str(item).strip())
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
