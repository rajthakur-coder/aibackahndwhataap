# Backend Structure

This backend follows the same package-first FastAPI style as `alignlabs-backend`.

```text
backend/
  run.py                 # Local uvicorn entrypoint
  worker.py              # Background worker entrypoint
  alembic.ini            # Alembic config, points to app/alembic
  requirements.txt       # Python dependencies
  .env.example           # Required environment variables
  pytest.ini             # Test configuration
  tests/                 # Backend tests
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
      automation/        # Templates, rules, events, executions
      contact/           # Contacts and tags
      crm/               # CRM models split by record type
        actions.py
        appointments.py
        bot_settings.py
        customers.py
        handoffs.py
        leads.py
        orders.py
      ecommerce/         # Connections, orders, products, customers, catalog, webhooks
      integration/       # Integration records and constants
      whatsapp/          # Messages, webhook events, credentials, templates
      audit.py
      knowledge.py
      user.py
    modules/             # Domain modules with router/schema/service files
      ai/                # Chat, intelligence, recommendations, search, tools, understanding
      audit/
      auth/
      automation/
        events/
        rules/
        runtime/
        shared/
        templates/
      crm/
        agent/
        agent_actions/
        handoffs/
        memory/
        records/
        settings/
      ecommerce/
        catalog/
        connections/
        orders/
        providers/
        shared/
        sync/
        webhooks/
      integrations/
        providers/
      knowledge/
      scraper/
        engine/
      system/
        system_service/
      whatsapp/
        core/            # WhatsApp Cloud API, live chat, webhook processing
    shared/              # Cross-cutting helpers and shared schemas
      lifecycle.py       # Startup/shutdown hooks
      middleware.py      # Request middleware
      schema_init.py     # Startup schema compatibility helpers
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
