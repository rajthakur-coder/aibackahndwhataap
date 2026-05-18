import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db.session import AsyncSessionLocal, get_db
from app.models.rag import KnowledgeDocument, ScrapedData, ScrapeJob
from app.modules.rag.rag_schema import DocumentRequest, ScrapeRequest
from app.modules.rag.rag_service import (
    crawl_website,
    pinecone_status,
    save_knowledge_chunks,
    save_knowledge_document,
    save_scraped_chunks,
    save_structured_extractions,
)


rag_router = APIRouter(tags=["rag"])


def _process_scraped_pages(db: Session, requested_url: str, pages: list[dict]) -> dict:
    saved_pages = []
    skipped_unchanged = 0
    total_chunks = 0
    pinecone_upserted = 0
    structured_counts = {"products": 0, "faqs": 0, "policies": 0, "services": 0}
    for page in pages:
        latest_saved_page = db.execute(
            select(ScrapedData)
            .where(ScrapedData.url == page["url"])
            .order_by(ScrapedData.created_at.desc())
        ).scalars().first()
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


def _process_scrape_job_sync(db, job_id: int) -> None:
    try:
        job = db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id)).scalars().first()
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
        job = db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id)).scalars().first()
        if job:
            job.status = "failed"
            job.error = str(exc)
            db.commit()


async def process_scrape_job(job_id: int) -> None:
    async with AsyncSessionLocal() as db:
        await db.run_sync(lambda sync_db: _process_scrape_job_sync(sync_db, job_id))


@rag_router.post("/scrape")
async def scrape(
    data: ScrapeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    try:
        job = ScrapeJob(url=data.url, max_pages=data.max_pages, status="queued")
        db.add(job)
        await db.commit()
        await db.refresh(job)
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


@rag_router.get("/scrape/jobs/{job_id}")
async def get_scrape_job(job_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
    job = result.scalars().first()
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


@rag_router.get("/scraped-data")
async def list_scraped_data(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ScrapedData).order_by(ScrapedData.created_at.desc()))
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "url": row.url,
            "content_length": len(row.content),
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@rag_router.post("/rag/rebuild")
async def rebuild_rag_index(db: AsyncSession = Depends(get_db)):
    return await db.run_sync(_rebuild_rag_index_sync)


def _rebuild_rag_index_sync(db):
    rows = db.execute(
        select(ScrapedData).order_by(ScrapedData.created_at.asc())
    ).scalars().all()
    documents = db.execute(
        select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.asc())
    ).scalars().all()
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


@rag_router.get("/rag/status")
def rag_status():
    return {
        "provider": "pinecone",
        "pinecone": pinecone_status(),
        "fallback": "local keyword/hash retrieval",
    }


@rag_router.post("/knowledge/documents")
async def add_knowledge_document(data: DocumentRequest, db: AsyncSession = Depends(get_db)):
    if not data.content.strip():
        raise HTTPException(status_code=400, detail="Document content is required")

    document = await db.run_sync(
        lambda sync_db: save_knowledge_document(
            sync_db,
            title=data.title,
            source=data.source,
            content=data.content,
        )
    )
    return {
        "status": "success",
        "id": document.id,
        "title": document.title,
        "source": document.source,
        "content_length": len(document.content),
    }


@rag_router.get("/knowledge/documents")
async def list_knowledge_documents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc()))
    rows = result.scalars().all()
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
