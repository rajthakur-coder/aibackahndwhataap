import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.models.entities import KnowledgeDocument, ScrapedData, ScrapeJob
from app.schemas import DocumentRequest, ScrapeRequest
from app.services.pinecone import status as pinecone_status
from app.services.rag import (
    save_knowledge_chunks,
    save_knowledge_document,
    save_scraped_chunks,
)
from app.services.scraper import crawl_website
from app.services.structured_extraction import save_structured_extractions


router = APIRouter(tags=["rag"])


def _process_scraped_pages(db: Session, requested_url: str, pages: list[dict]) -> dict:
    saved_pages = []
    skipped_unchanged = 0
    total_chunks = 0
    pinecone_upserted = 0
    structured_counts = {"products": 0, "faqs": 0, "policies": 0, "services": 0}
    for page in pages:
        latest_saved_page = (
            db.query(ScrapedData)
            .filter(ScrapedData.url == page["url"])
            .order_by(ScrapedData.created_at.desc())
            .first()
        )
        if latest_saved_page and latest_saved_page.content == page["content"]:
            skipped_unchanged += 1
            saved_pages.append(
                {
                    "id": latest_saved_page.id,
                    "url": latest_saved_page.url,
                    "title": page.get("title"),
                    "content_length": len(latest_saved_page.content),
                    "images": len(page.get("image_urls") or []),
                    "social_links": len(page.get("social_links") or []),
                    "status": "unchanged",
                    "structured": {"products": 0, "faqs": 0, "policies": 0, "services": 0},
                }
            )
            continue

        row = ScrapedData(url=page["url"], content=page["content"])
        db.add(row)
        db.commit()
        db.refresh(row)
        rag_result = save_scraped_chunks(db, row)
        total_chunks += rag_result["chunks"]
        pinecone_upserted += rag_result["pinecone_upserted"]
        extraction_result = save_structured_extractions(db, row)
        for key, value in extraction_result.items():
            structured_counts[key] += value
        saved_pages.append(
            {
                "id": row.id,
                "url": row.url,
                "title": page.get("title"),
                "content_length": len(row.content),
                "images": len(page.get("image_urls") or []),
                "social_links": len(page.get("social_links") or []),
                "status": "updated",
                "structured": extraction_result,
            }
        )

    return {
        "status": "success",
        "requested_url": requested_url,
        "pages_scraped": len(saved_pages),
        "pages_updated": len(saved_pages) - skipped_unchanged,
        "skipped_unchanged": skipped_unchanged,
        "chunk_count": total_chunks,
        "pinecone_upserted": pinecone_upserted,
        "structured": structured_counts,
        "pages": saved_pages,
    }


def process_scrape_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.query(ScrapeJob).filter(ScrapeJob.id == job_id).first()
        if not job:
            return

        job.status = "running"
        db.commit()

        pages = crawl_website(job.url, job.max_pages)
        if not pages:
            raise ValueError("Scrape failed: no readable pages found")

        result = _process_scraped_pages(db, job.url, pages)
        job.status = "completed"
        job.result = json.dumps(result)
        job.error = None
        db.commit()
    except Exception as exc:
        job = db.query(ScrapeJob).filter(ScrapeJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(exc)
            db.commit()
    finally:
        db.close()


@router.post("/scrape")
async def scrape(
    data: ScrapeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        job = ScrapeJob(url=data.url, max_pages=data.max_pages, status="queued")
        db.add(job)
        db.commit()
        db.refresh(job)
        background_tasks.add_task(process_scrape_job, job.id)
        return {
            "status": "queued",
            "job_id": job.id,
            "url": job.url,
            "max_pages": job.max_pages,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Scrape failed: {exc}") from exc


@router.get("/scrape/jobs/{job_id}")
def get_scrape_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(ScrapeJob).filter(ScrapeJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Scrape job not found")

    result = None
    if job.result:
        try:
            result = json.loads(job.result)
        except json.JSONDecodeError:
            result = None

    return {
        "id": job.id,
        "url": job.url,
        "max_pages": job.max_pages,
        "status": job.status,
        "result": result,
        "error": job.error,
        "created_at": str(job.created_at),
        "updated_at": str(job.updated_at),
    }


@router.get("/scraped-data")
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


@router.post("/rag/rebuild")
def rebuild_rag_index(db: Session = Depends(get_db)):
    rows = db.query(ScrapedData).order_by(ScrapedData.created_at.asc()).all()
    documents = db.query(KnowledgeDocument).order_by(KnowledgeDocument.created_at.asc()).all()
    total_chunks = 0
    pinecone_upserted = 0
    structured_counts = {"products": 0, "faqs": 0, "policies": 0, "services": 0}

    for row in rows:
        result = save_scraped_chunks(db, row)
        total_chunks += result["chunks"]
        pinecone_upserted += result["pinecone_upserted"]
        extraction_result = save_structured_extractions(db, row)
        for key, value in extraction_result.items():
            structured_counts[key] += value
    for document in documents:
        result = save_knowledge_chunks(db, document)
        total_chunks += result["chunks"]
        pinecone_upserted += result["pinecone_upserted"]

    return {
        "status": "success",
        "scraped_documents": len(rows),
        "knowledge_documents": len(documents),
        "chunks": total_chunks,
        "pinecone_upserted": pinecone_upserted,
        "structured": structured_counts,
    }


@router.get("/rag/status")
def rag_status():
    return {
        "provider": "pinecone",
        "pinecone": pinecone_status(),
        "fallback": "local keyword/hash retrieval",
    }


@router.post("/knowledge/documents")
def add_knowledge_document(data: DocumentRequest, db: Session = Depends(get_db)):
    if not data.content.strip():
        raise HTTPException(status_code=400, detail="Document content is required")

    document = save_knowledge_document(
        db,
        title=data.title,
        source=data.source,
        content=data.content,
    )
    return {
        "status": "success",
        "id": document.id,
        "title": document.title,
        "source": document.source,
        "content_length": len(document.content),
    }


@router.get("/knowledge/documents")
def list_knowledge_documents(db: Session = Depends(get_db)):
    rows = db.query(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc()).all()
    return [
        {
            "id": row.id,
            "title": row.title,
            "source": row.source,
            "content_length": len(row.content),
            "created_at": str(row.created_at),
        }
        for row in rows
    ]
