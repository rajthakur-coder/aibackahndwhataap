from pydantic import BaseModel


class ScrapeRequest(BaseModel):
    url: str
    max_pages: int = 20


class DocumentRequest(BaseModel):
    title: str
    content: str
    source: str | None = None
