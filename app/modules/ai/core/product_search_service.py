import re
from collections import Counter
from difflib import SequenceMatcher


TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "batao",
    "bhejo",
    "chahiye",
    "chaiye",
    "de",
    "dekhao",
    "dekhna",
    "dikha",
    "dika",
    "dikhai",
    "dikhana",
    "dikhao",
    "do",
    "for",
    "hai",
    "hain",
    "have",
    "i",
    "in",
    "is",
    "ka",
    "ke",
    "ki",
    "ko",
    "koi",
    "kya",
    "me",
    "mein",
    "mujhe",
    "of",
    "on",
    "please",
    "product",
    "products",
    "show",
    "the",
    "to",
    "under",
    "wala",
    "wale",
    "wali",
    "want",
    "with",
}

ALIASES = {
    "joota": {"shoe", "shoes", "footwear"},
    "jute": {"shoe", "shoes", "footwear"},
    "juta": {"shoe", "shoes", "footwear"},
    "joote": {"shoe", "shoes", "footwear"},
    "shoo": {"shoe", "shoes"},
    "shoos": {"shoe", "shoes"},
    "sneakar": {"sneaker", "sneakers", "shoe", "shoes"},
    "sneaker": {"sneaker", "sneakers", "shoe", "shoes"},
    "mobile": {"mobile", "phone", "smartphone"},
    "fone": {"phone", "mobile", "smartphone"},
    "phone": {"phone", "mobile", "smartphone"},
    "kapda": {"clothing", "apparel", "dress"},
    "kapde": {"clothing", "apparel", "dress"},
    "tshirt": {"tshirt", "shirt", "t", "tee"},
    "tee": {"tshirt", "shirt", "tee"},
    "watch": {"watch", "watches"},
}


def search_terms(text: str) -> Counter:
    terms = Counter()
    for token in _tokens(text):
        if token in STOP_WORDS or len(token) <= 1:
            continue
        for expanded in _expand_token(token):
            if expanded not in STOP_WORDS and len(expanded) > 1:
                terms[expanded] += 1
    return terms


def score_search_text(query_terms: Counter, text: str) -> float:
    if not query_terms:
        return 0.2

    text_tokens = _tokens(text)
    if not text_tokens:
        return 0.0

    text_terms = Counter(text_tokens)
    token_set = set(text_tokens)
    score = 0.0

    for term, weight in query_terms.items():
        if term in text_terms:
            score += 3.0 * text_terms[term] * weight
            continue

        if _singular(term) in text_terms:
            score += 2.4 * weight
            continue

        if _prefix_or_substring_match(term, token_set):
            score += 1.5 * weight
            continue

        if _fuzzy_match(term, token_set):
            score += 1.0 * weight

    return score


def product_search_text(product) -> str:
    if isinstance(product, dict):
        values = [
            product.get("title"),
            product.get("description"),
            product.get("category"),
            product.get("brand"),
            product.get("tags"),
            product.get("product_type"),
            product.get("sku"),
        ]
    else:
        values = [
            getattr(product, "title", None),
            getattr(product, "description", None),
            getattr(product, "vendor", None),
            getattr(product, "product_type", None),
            getattr(product, "tags", None),
            getattr(product, "collections", None),
            getattr(product, "sku", None),
            getattr(product, "skus", None),
            getattr(product, "seo_title", None),
            getattr(product, "seo_description", None),
        ]
    return " ".join(str(value or "") for value in values)


def _tokens(text: str) -> list[str]:
    normalized = (text or "").lower().replace("t-shirt", "tshirt")
    return [token.lower() for token in TOKEN_RE.findall(normalized)]


def _expand_token(token: str) -> set[str]:
    expanded = {token, _singular(token)}
    expanded.update(ALIASES.get(token, set()))
    for alias in list(expanded):
        expanded.add(_singular(alias))
    return expanded


def _singular(token: str) -> str:
    if token == "shoes":
        return "shoe"
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and (
        (token.endswith("es") and token[-3:] in {"ses", "xes", "zes"})
        or token.endswith(("ches", "shes"))
    ):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _prefix_or_substring_match(term: str, tokens: set[str]) -> bool:
    if len(term) < 4:
        return False
    return any(
        token.startswith(term)
        or term.startswith(token)
        or term in token
        or token in term
        for token in tokens
        if len(token) >= 4
    )


def _fuzzy_match(term: str, tokens: set[str]) -> bool:
    if len(term) < 5:
        return False
    return any(
        abs(len(term) - len(token)) <= 2
        and SequenceMatcher(None, term, token).ratio() >= 0.82
        for token in tokens
        if len(token) >= 5
    )
