# saas/ — placeholder

This folder is a stake in the ground. It marks where the hosted SaaS
will live until it gets its own repo (see Phase 3 of
[../docs/MIGRATION_PLAN_TO_SAAS.md](../docs/MIGRATION_PLAN_TO_SAAS.md)).

**Nothing here is wired into the running app.** The Docker-based local
alpha continues to be the source of truth.

## When to start filling this in

Phase 3 of the migration plan. At that point we either:

- start adding files here (frontend in `saas/web/`, backend in
  `saas/api/`, schema in `saas/db/`), or
- create a sibling repo `flow-bof-saas/` and delete this folder.

The decision depends on whether we want the SaaS to share git history
with the automation core. Recommended: separate repo, since the
hosted product and the local automation will have very different
release cadences and dependency footprints.

## Design docs

- [../docs/SAAS_ARCHITECTURE.md](../docs/SAAS_ARCHITECTURE.md) — overall hosted system.
- [../docs/JOB_PROTOCOL.md](../docs/JOB_PROTOCOL.md) — wire format with the agent.
- [../docs/MIGRATION_PLAN_TO_SAAS.md](../docs/MIGRATION_PLAN_TO_SAAS.md) — how we get from today to there.

## Stack (recommended, see SAAS_ARCHITECTURE.md for rationale)

- Frontend: Next.js (App Router) + Tailwind.
- Backend: FastAPI (preferred; ports current Python directly).
- Database: Postgres.
- Queue: Postgres-backed for MVP, Redis/BullMQ later.
- Object storage: S3 / Cloudflare R2 / Supabase Storage.
- Auth: Clerk / Supabase / Auth.js — choose at start of Phase 3.

## Do not put here

- Anything that would import from the agent or the local Streamlit app.
  This boundary is what makes the SaaS deployable independently.
- User browser cookies or browser-profile data. Per the security model:
  the SaaS **never** holds these.
- Files needed by the Docker-compose stack today. The current alpha
  must keep working with zero changes here.
