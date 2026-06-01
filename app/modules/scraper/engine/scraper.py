import logging
from app.config import settings
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger("firecrawl_scraper")
logger.setLevel(logging.INFO)

def get_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def to_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return {}


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        if isinstance(x, str):
            x = x.strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ----------------------------
# Logo picking (no PIL; heuristic only)
# ----------------------------
def _is_svg(u: str) -> bool:
    try:
        return urlparse(u).path.lower().endswith(".svg")
    except Exception:
        return False


def _looks_like_favicon(u: str) -> bool:
    ul = (u or "").lower()
    return any(k in ul for k in ["favicon", "apple-touch-icon", "site-icon"])


def _score_logo_url(u: str) -> int:
    """Higher score = better logo candidate."""
    if not u:
        return -10_000

    ul = u.lower()
    score = 0

    if _is_svg(u):
        score += 10_000  # crisp at any size

    if "logo" in ul:
        score += 500

    if _looks_like_favicon(u):
        score -= 5_000

    # prefer common higher-res icon sizes if present in URL
    for token, pts in [
        ("512", 300),
        ("384", 250),
        ("256", 200),
        ("192", 150),
        ("128", 100),
        ("96", 50),
        ("64", 20),
        ("32", -50),
        ("16", -100),
    ]:
        if token in ul:
            score += pts
            break

    return score


def _pick_best_logo(candidates: List[str]) -> Optional[str]:
    candidates = _dedupe_keep_order(
        [c for c in candidates if isinstance(c, str) and c.strip()]
    )
    if not candidates:
        return None

    best = None
    best_score = -10_000_000
    for c in candidates:
        s = _score_logo_url(c)
        if s > best_score:
            best_score = s
            best = c
    return best


# ----------------------------
# Image Filtering (Layer 1)
# ----------------------------
def _clean_image_urls(raw_urls: List[str]) -> List[str]:
    """
    Layer 1: Filter out SVGs, Base64 strings, and junk keyword URLs
    instantly to save bandwidth and DB space.
    """
    junk_keywords = ["icon", "avatar", "logo", "pixel", "tracking", "badge", "button", "profile"]
    junk_extensions = [".svg", ".gif"]
    clean_urls = []
    seen = set()

    for img in raw_urls:
        if not isinstance(img, str) or not img.strip():
            continue
        
        # 1. Drop inline base64 images instantly
        if img.startswith("data:image"):
            continue

        img_lower = img.lower()
        
        # 2. Check for junk extensions
        if any(ext in img_lower for ext in junk_extensions) or any(f"{ext}?" in img_lower for ext in junk_extensions):
            continue
            
        # 3. Check for junk keywords in the path
        if any(kw in img_lower for kw in junk_keywords):
            continue

        # 4. Add if unique
        if img not in seen:
            seen.add(img)
            clean_urls.append(img)
            
    return clean_urls



SOCIAL_HOST_TO_TYPE = {
    "twitter.com": "twitter",
    "x.com": "twitter",
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
}


def _canonicalize_url(u: str) -> str:
    """Remove fragments, trim trailing slash. Keep query."""
    try:
        u = (u or "").strip()
        if not u:
            return ""

        if "://" not in u and u.startswith("www."):
            u = "https://" + u

        p = urlparse(u)
        if not p.scheme or not p.netloc:
            return u

        p = p._replace(fragment="")
        p = p._replace(path=(p.path or "").rstrip("/"))

        return urlunparse(p).strip()
    except Exception:
        return (u or "").strip()


def _infer_social_type(u: str) -> Optional[str]:
    """Match exact host or subdomain host."""
    try:
        host = urlparse(u).netloc.lower()
        if host.startswith("www."):
            host = host[4:]

        if host in SOCIAL_HOST_TO_TYPE:
            return SOCIAL_HOST_TO_TYPE[host]

        for base, typ in SOCIAL_HOST_TO_TYPE.items():
            if host == base or host.endswith("." + base):
                return typ

        return None
    except Exception:
        return None


def _socials_from_links(raw_links: Any) -> List[Dict[str, str]]:
    links: List[str] = []

    if isinstance(raw_links, list):
        links = [x for x in raw_links if isinstance(x, str)]
    elif isinstance(raw_links, dict):
        for v in raw_links.values():
            if isinstance(v, list):
                links.extend([x for x in v if isinstance(x, str)])
            elif isinstance(v, str):
                links.append(v)
    elif isinstance(raw_links, str):
        links = [raw_links]

    out: List[Dict[str, str]] = []
    seen = set()

    for u in links:
        u = _canonicalize_url(u)
        if not u or u in seen:
            continue

        s_type = _infer_social_type(u)
        if not s_type:
            continue

        ul = u.lower()
        if any(x in ul for x in ["intent/tweet", "sharer.php", "/share", "share?"]):
            continue

        out.append({"type": s_type, "url": u})
        seen.add(u)

        if len(out) >= 15:
            break

    return out


# ----------------------------
# Main API
# ----------------------------
def scrape_brand_fields_only(url: str) -> Dict[str, Any]:
    """
    Returns ONLY:
      website_link, logo, fonts, color_palette, socials, page_images
    """
    api_key = settings.FIRECRAWL_API_KEY
    if not api_key:
        raise RuntimeError("Missing FIRECRAWL_API_KEY environment variable")

    logger.info(f"🚀 Starting Firecrawl scrape for: {url}")
    from firecrawl import Firecrawl

    app = Firecrawl(api_key=api_key)

    try:
        result = app.scrape(
            url=url,
            formats=[
                "branding",
                "links",
                "images",
            ],
            only_main_content=False,
            timeout=30000,
        )

        data = get_attr_or_key(result, "data", result) or {}
        data = to_dict(data)

        branding = to_dict(data.get("branding") or {})
        metadata = to_dict(data.get("metadata") or {})
        images = to_dict(branding.get("images") or {})

        # Logo
        candidates = [
            branding.get("logo"),
            images.get("logo"),
            metadata.get("ogImage") or metadata.get("og_image"),
            images.get("favicon"),
            metadata.get("favicon"),
        ]
        logo = _pick_best_logo(
            [c for c in candidates if isinstance(c, str) and c.strip()]
        )

        # Fonts
        fonts: List[str] = []
        raw_fonts = branding.get("fonts") or []
        if isinstance(raw_fonts, list):
            for f in raw_fonts:
                if isinstance(f, dict):
                    fam = f.get("family")
                    if fam:
                        fonts.append(str(fam).strip())
                elif isinstance(f, str) and f.strip():
                    fonts.append(f.strip())
        fonts = _dedupe_keep_order(fonts)

        # Colors
        color_palette: List[str] = []
        raw_colors = branding.get("colors") or {}
        colors_dict = to_dict(raw_colors)
        for v in colors_dict.values():
            if isinstance(v, str) and v.strip().startswith("#"):
                color_palette.append(v.strip())
        color_palette = _dedupe_keep_order(color_palette)

        # Socials
        socials = _socials_from_links(data.get("links"))
        
        # ----------------------------
        # Images (Cleaned & Capped)
        # ----------------------------
        page_images: List[str] = []
        raw_images = data.get("images")
        
        if isinstance(raw_images, list):
            # Apply Layer 1 heuristics
            page_images = _clean_image_urls(raw_images)
            
        # Strict cap at 30 before returning
        page_images = page_images[:30]

        return {
            "website_link": url,
            "logo": logo,
            "fonts": fonts,
            "color_palette": color_palette,
            "socials": socials,
            "page_images": page_images,
        }

    except Exception as e:
        logger.error(f"❌ Firecrawl scrape failed: {e}", exc_info=True)
        return {
            "website_link": url,
            "logo": None,
            "fonts": [],
            "color_palette": [],
            "socials": [],
            "page_images": [],
        }
