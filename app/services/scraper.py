import json
import re
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup


REQUEST_TIMEOUT = 25
MAX_CONTENT_CHARS = 50000
MAX_PAGES = 80
MAX_LINKS_PER_PAGE = 120
MAX_SECTION_CHARS = 5000
SOCIAL_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "pinterest.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "youtube.com",
)
IMPORTANT_PATH_KEYWORDS = (
    "about",
    "catalog",
    "collection",
    "contact",
    "faq",
    "help",
    "policy",
    "privacy",
    "product",
    "refund",
    "return",
    "shipping",
    "shop",
    "terms",
)
PAGE_TYPE_KEYWORDS = {
    "product": ("product", "products", "shop", "item"),
    "collection": ("collection", "collections", "category", "catalog"),
    "faq": ("faq", "help", "support"),
    "policy": ("policy", "privacy", "terms", "refund", "return", "shipping"),
    "contact": ("contact",),
    "about": ("about",),
}


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be a valid http or https URL")


def _normalize_url(url: str) -> str:
    clean, _fragment = urldefrag(url)
    parsed = urlparse(clean)
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(path=path, query=parsed.query.rstrip("&"), fragment="").geturl()


def _same_domain(url: str, root_netloc: str) -> bool:
    return urlparse(url).netloc.lower().removeprefix("www.") == root_netloc.lower().removeprefix("www.")


def _is_http_url(url: str) -> bool:
    return urlparse(url).scheme in {"http", "https"} and bool(urlparse(url).netloc)


def _score_url(url: str) -> int:
    path = urlparse(url).path.lower()
    return sum(1 for keyword in IMPORTANT_PATH_KEYWORDS if keyword in path)


def _page_type(url: str) -> str:
    path = urlparse(url).path.lower()
    for page_type, keywords in PAGE_TYPE_KEYWORDS.items():
        if any(keyword in path for keyword in keywords):
            return page_type
    return "general"


def _category_from_url(url: str) -> str:
    parts = [part for part in urlparse(url).path.strip("/").split("/") if part]
    if len(parts) >= 2 and parts[0].lower() in {"collections", "collection", "category", "catalog"}:
        return parts[1].replace("-", " ")
    if len(parts) >= 2 and parts[0].lower() in {"products", "product", "shop"}:
        return parts[0].lower()
    return ""


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _json_ld_objects(soup: BeautifulSoup) -> list[dict]:
    objects = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            objects.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                objects.extend(item for item in graph if isinstance(item, dict))
            objects.append(data)
    return objects


def _first_price(text: str, json_ld: list[dict]) -> str:
    for item in json_ld:
        offers = item.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if isinstance(offers, dict):
            price = offers.get("price") or offers.get("lowPrice")
            currency = offers.get("priceCurrency")
            if price:
                return f"{currency or ''} {price}".strip()

    match = re.search(r"(?:rs\.?|inr|₹|\$)\s?[\d,]+(?:\.\d{1,2})?", text, flags=re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _brand(soup: BeautifulSoup, json_ld: list[dict]) -> str:
    for item in json_ld:
        brand = item.get("brand")
        if isinstance(brand, dict) and brand.get("name"):
            return str(brand["name"]).strip()
        if isinstance(brand, str):
            return brand.strip()
    return _meta_content(soup, "og:site_name", "application-name")


def _page_metadata(url: str, soup: BeautifulSoup, title: str, page_text: str, json_ld: list[dict]) -> dict:
    return {
        "page_type": _page_type(url),
        "category": _category_from_url(url),
        "price": _first_price(page_text, json_ld),
        "brand": _brand(soup, json_ld),
        "title": title,
    }


def _section_title(tag) -> str:
    heading = tag.find(["h1", "h2", "h3", "h4"])
    if heading:
        return heading.get_text(" ", strip=True)[:200]
    label = tag.get("aria-label") or tag.get("id") or ""
    return str(label).replace("-", " ").replace("_", " ").strip()[:200]


def _extract_sections(soup: BeautifulSoup) -> list[dict]:
    roots = soup.find_all(["article", "section"])
    if not roots:
        roots = soup.find_all(["main"])
    if not roots and soup.body:
        roots = [soup.body]

    sections = []
    seen_text = set()
    for tag in roots:
        title = _section_title(tag) or "Page content"
        text = tag.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        section_text = "\n".join(lines)[:MAX_SECTION_CHARS]
        text_key = re.sub(r"\s+", " ", section_text.lower())[:500]
        if len(section_text) < 30 or text_key in seen_text:
            continue
        seen_text.add(text_key)
        sections.append({"heading": title, "text": section_text})

    return sections


def _fetch(url: str) -> requests.Response:
    response = requests.get(
        url,
        headers={"User-Agent": "AIWhatsAppAutomationBot/1.0"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response


def _sitemap_urls(root_url: str, root_netloc: str, max_pages: int) -> list[str]:
    sitemap_url = urljoin(root_url, "/sitemap.xml")
    try:
        response = _fetch(sitemap_url)
    except requests.RequestException:
        return []

    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError:
        return []

    urls = []
    for element in root.iter():
        if not element.tag.endswith("loc") or not element.text:
            continue
        candidate = _normalize_url(element.text.strip())
        if _is_http_url(candidate) and _same_domain(candidate, root_netloc):
            urls.append(candidate)
        if len(urls) >= max_pages:
            break

    return sorted(set(urls), key=lambda item: (-_score_url(item), item))[:max_pages]


def _extract_page(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    json_ld = _json_ld_objects(soup)
    title = (soup.title.string.strip() if soup.title and soup.title.string else "")[:300]
    description = ""
    meta_description = soup.find("meta", attrs={"name": "description"})
    if meta_description:
        description = (meta_description.get("content") or "").strip()

    links = []
    social_links = []
    image_urls = []
    for tag in soup.find_all("a", href=True):
        href = urljoin(url, tag["href"])
        if not _is_http_url(href):
            continue
        href = _normalize_url(href)
        links.append(href)
        if any(domain in urlparse(href).netloc.lower() for domain in SOCIAL_DOMAINS):
            social_links.append(href)

    for tag in soup.find_all("img"):
        src = tag.get("src") or tag.get("data-src") or tag.get("data-original")
        if src:
            image_urls.append(_normalize_url(urljoin(url, src)))

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    page_text = "\n".join(lines)
    page_metadata = _page_metadata(url, soup, title, page_text, json_ld)
    sections = _extract_sections(soup)
    if not sections:
        sections = [{"heading": "Page content", "text": page_text[:MAX_SECTION_CHARS]}]

    metadata_lines = [
        f"Page URL: {url}",
        f"Page title: {title}",
        "Page metadata: " + json.dumps(page_metadata, ensure_ascii=True),
    ]
    if description:
        metadata_lines.append(f"Meta description: {description}")
    if social_links:
        metadata_lines.append("Social accounts: " + ", ".join(sorted(set(social_links))[:20]))
    if image_urls:
        metadata_lines.append("Images: " + ", ".join(sorted(set(image_urls))[:40]))

    section_text = "\n\n".join(
        f"Section: {section['heading']}\n{section['text']}"
        for section in sections
    )

    return {
        "url": url,
        "title": title,
        "content": "\n".join(metadata_lines + ["", section_text])[:MAX_CONTENT_CHARS],
        "links": links,
        "social_links": sorted(set(social_links)),
        "image_urls": sorted(set(image_urls)),
        "metadata": page_metadata,
        "sections": sections,
    }


def scrape_website(url: str) -> str:
    _validate_url(url)
    response = _fetch(url)
    return _extract_page(_normalize_url(url), response.text)["content"]


def crawl_website(url: str, max_pages: int = MAX_PAGES) -> list[dict]:
    _validate_url(url)
    max_pages = max(1, min(max_pages, MAX_PAGES))
    root_url = _normalize_url(url)
    root_netloc = urlparse(root_url).netloc

    queue = deque([root_url])
    for sitemap_url in _sitemap_urls(root_url, root_netloc, max_pages):
        if sitemap_url != root_url:
            queue.append(sitemap_url)

    seen = set()
    pages = []
    while queue and len(pages) < max_pages:
        current = queue.popleft()
        if current in seen or not _same_domain(current, root_netloc):
            continue
        seen.add(current)

        try:
            response = _fetch(current)
        except requests.RequestException:
            continue

        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type and content_type:
            continue

        page = _extract_page(current, response.text)
        pages.append(page)

        internal_links = [
            link
            for link in page["links"]
            if link not in seen and _same_domain(link, root_netloc)
        ]
        internal_links = sorted(set(internal_links), key=lambda item: (-_score_url(item), item))
        queue.extend(internal_links[:MAX_LINKS_PER_PAGE])

    return pages

