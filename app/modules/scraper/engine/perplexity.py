import json
import requests
import logging
from typing import Dict, Any
from app.config import settings
logger = logging.getLogger("perplexity_scraper")
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - [PERPLEXITY] - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


PERPLEXITY_API_KEY = settings.PERPLEXITY_API_KEY
API_URL = "https://api.perplexity.ai/chat/completions"


def get_brand_intelligence(url: str) -> Dict[str, Any]:
    """
    Uses Perplexity (Sonar-Pro) with enhanced social discovery logic.
    """
    logger.info(f"🚀 Starting Perplexity analysis for URL: {url}")

    if not PERPLEXITY_API_KEY:
        logger.error("❌ PERPLEXITY_API_KEY is missing.")
        return {}

    # 1. Define Expanded Schema Structure
    schema_structure = {
        "company_name": "Official name of the brand",
        "industry": "Specific industry category",
        "about_company": "Professional summary (80-120 words).",
        "target_demographics": "Description of the target audience.",
        "website_link": "The canonical URL.",
        "competitors": [{"name": "Name", "url": "URL"}],
        "socials": [
            {"platform": "linkedin", "url": "URL"},
            {"platform": "instagram", "url": "URL"},
            {"platform": "facebook", "url": "URL"},
            {"platform": "youtube", "url": "URL"},
            {"platform": "tiktok", "url": "URL"},
            {"platform": "twitter", "url": "URL"},
        ],
    }

    system_prompt = f"""
    You are an expert brand analyst API. 
    Analyze the provided website URL and its online presence to extract brand data.
    
    SOCIAL MEDIA GUIDELINES:
    1. Search for official social media profiles on: LinkedIn, Instagram, X (Twitter), Facebook, TikTok, and YouTube.
    2. Ensure the URLs are the official handles for THIS specific brand.
    3. If a profile exists but isn't linked on the homepage, search the web to find the verified account.
    4. Return an empty list for 'socials' if absolutely none are found, but prioritize thoroughness.

    GENERAL GUIDELINES:
    1. Output ONLY valid JSON. No markdown formatting.
    2. Find exactly 5 direct competitors.
    3. Follow this JSON schema exactly:
    {json.dumps(schema_structure)}
    """

    user_prompt = f"Perform a deep search and analyze the brand at this URL: {url}. Extract all official social media links and 5 competitors."

    payload = {
        "model": "sonar-pro",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
    }

    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        logger.info("⏳ Sending request to Perplexity API...")
        response = requests.post(API_URL, json=payload, headers=headers, timeout=60)

        if response.status_code != 200:
            logger.error(f"❌ API Error: {response.text}")
            return {}

        result = response.json()
        content = result["choices"][0]["message"]["content"]

        # Clean Markdown if present
        if "```" in content:
            content = content.split("```json")[-1].split("```")[0].strip()

        parsed_data = json.loads(content)

        # Log discovery summary
        social_platforms = [s.get("platform") for s in parsed_data.get("socials", [])]
        logger.info(
            f"✅ Success! Found socials for: {', '.join(social_platforms) if social_platforms else 'None'}"
        )

        return parsed_data

    except Exception as e:
        logger.error(f"❌ Analysis failed: {e}")
        return {}
