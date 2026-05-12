from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup


REQUEST_TIMEOUT = 25
MAX_CONTENT_CHARS = 50000
MAX_PAGES = 80
MAX_LINKS_PER_PAGE = 120
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

    metadata_lines = [
        f"Page URL: {url}",
        f"Page title: {title}",
    ]
    if description:
        metadata_lines.append(f"Meta description: {description}")
    if social_links:
        metadata_lines.append("Social accounts: " + ", ".join(sorted(set(social_links))[:20]))
    if image_urls:
        metadata_lines.append("Images: " + ", ".join(sorted(set(image_urls))[:40]))

    return {
        "url": url,
        "title": title,
        "content": "\n".join(metadata_lines + ["", page_text])[:MAX_CONTENT_CHARS],
        "links": links,
        "social_links": sorted(set(social_links)),
        "image_urls": sorted(set(image_urls)),
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
