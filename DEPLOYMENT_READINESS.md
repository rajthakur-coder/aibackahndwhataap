# Deployment Readiness

## Required production checks

- `GET /health` returns healthy.
- `GET /readiness?tenant_id=<tenant>` returns `status=ready`.
- `POST /runtime/ths-config` has been run for the THS tenant.
- `POST /automations/templates/ths-outbound/seed` has been run.
- `POST /automations/templates/ths-outbound/submit-meta` has been run at least 7 days before launch.
- `GET /automations/templates/ths-outbound/approval-status` shows all required templates approved.

## Required environment

- `DATABASE_URI`
- `REDIS_URL`
- `SECRET_KEY`
- `OPENROUTER_API_KEY`
- `ACCESS_TOKEN`
- `PHONE_NUMBER_ID`
- `VERIFY_TOKEN`
- `ECOMMERCE_TOKEN_SECRET`

## Recommended environment

- `SHIPROCKET_TOKEN`
- `SLACK_WEBHOOK_URL`
- `GMAIL_ID`
- `GMAIL_APP_PASSWORD`

## Security posture

- `DEBUG=false`
- `COOKIE_SECURE=true`
- `CORS_ORIGINS` set to concrete frontend origins, not `*`
- RLS migrations applied for tenant tables
- Webhook secrets configured before live traffic
