# Backend Structure

This backend is organized as a package-first FastAPI application.

```text
backend/
  app/
    api/
      routes/          # Domain route modules
        crm.py
        ecommerce.py
        rag.py
        system.py
        whatsapp.py
    core/
      config.py        # Environment loading and app settings
    db/
      session.py       # SQLAlchemy engine, SessionLocal, Base, get_db
    models/
      entities.py      # SQLAlchemy models
    schemas/
      requests.py      # Pydantic request schemas
    services/
      agent.py         # CRM/lead/appointment agent logic
      ecommerce.py     # Ecommerce sync/order/product logic
      ecommerce_sync.py # Scheduled ecommerce sync orchestration
      intelligence.py   # Query intent and policy type detection
      structured_extraction.py # AI/heuristic extraction into structured tables
      conversation_memory.py # Last question/product memory helpers
      messages.py      # Conversation message persistence
      openai_chat.py   # OpenRouter chat replies
      pinecone.py      # Pinecone vector storage/retrieval
      rag.py           # RAG chunking/retrieval/image lookup
      sales_recommendations.py # Product recommendations and WhatsApp catalog suggestions
      scraper.py       # Website crawling/scraping
      serializers.py   # Response serialization helpers
      webhook_processor.py # WhatsApp webhook processing flow
      whatsapp.py      # WhatsApp Cloud API messaging
    shared/            # Cross-cutting helpers shared by domains
    main.py            # FastAPI app wiring and router registration
```

Use `uvicorn app.main:app` for the package entrypoint.
