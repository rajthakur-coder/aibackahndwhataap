# Backend Structure

This backend follows the same package-first FastAPI style as `alignlabs-backend`.

```text
backend/
  run.py                 # Local uvicorn entrypoint
  worker.py              # Background worker entrypoint placeholder
  main.py                # Compatibility entrypoint that imports app.main
  alembic.ini            # Alembic config, points to app/alembic
  requirements.txt       # Python dependencies
  .env.example           # Required environment variables
  app/
    __init__.py
    config.py            # Environment loading and app settings
    main.py              # FastAPI app wiring, router registration, startup loops
    alembic/             # Database migrations
      env.py
      script.py.mako
      versions/
    db/
      base.py            # Imports models for Alembic metadata
      mixins.py          # Shared SQLAlchemy mixins
      session.py         # SQLAlchemy engine, SessionLocal, Base, get_db
    models/              # SQLAlchemy models grouped by domain
      automation.py
      contact.py
      crm.py
      ecommerce.py
      whatsapp.py
    modules/             # Domain modules with router/schema/service files
      ai/
        core/            # AI routing, query understanding, recommendations
      automation/
        core/            # Automation scheduling and execution internals
      crm/
        core/            # CRM agent and customer memory internals
      ecommerce/
        core/            # Shopify/WooCommerce sync, cache, serializers
      system/
      whatsapp/
        core/            # WhatsApp Cloud API, live chat, webhook processing
    shared/              # Cross-cutting helpers and shared schemas
    security/            # Auth/security dependencies
    queue/               # Queue/worker integration points
    mail_templates/      # Email template package placeholder
    utils/               # Shared utility package placeholder
```

Module convention:

```text
app/modules/<domain>/
  <domain>_router.py     # FastAPI endpoints
  <domain>_schema.py     # Pydantic request/response schemas
  <domain>_service.py    # Public service facade used by routers
  core/                  # Larger internal domain logic, clients, processors
```

Use `uvicorn app.main:app` or `python run.py` for the package entrypoint.
