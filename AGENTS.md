# AGENTS.md

FastAPI + LangGraph booking app for a small nail/hair salon in Vietnam. Backend only so far
(Plan 1–2 done: 281 tests green). Next: Plan 3 (Telegram bot), Plan 4 (React frontend).

## Read first (in this order)

1. `CONTEXT.md` — the *why*: locked decisions, 21 already-fixed traps (do NOT "clean them up"),
   UI constraints for elderly users, security audit results. Authoritative.
2. `RUNBOOK.md` — the *how*: setup, test accounts, Azure/Langfuse config, troubleshooting.
3. Before any plan task: the **Ràng buộc toàn cục** section in that plan's `README.md`
   under `docs/superpowers/plans/`. Work is executed TDD via the `superpowers` skills.

## Commands

```bash
uv venv && uv pip install -r requirements.txt   # requirements.txt is the real dep source
cp .env.example .env                             # then fill in keys
docker compose up -d mongo                       # tests AND local run need Mongo at :27017
uvicorn main:app --reload                        # Swagger at /docs

pytest                                           # full suite; needs Mongo running
pytest tests/test_tools.py -k booking            # single test
pytest tests/test_timeparse_llm.py -m llm -v     # calls real Azure ($$$); excluded by default
```

- Tests use DB `chatbot_test_db`, auto-wiped per test (`conftest.py`). `asyncio_mode = "auto"`.
- No CI, no pre-commit hooks. ruff/black/mypy are configured in `pyproject.toml`
  but not enforced — run them only if asked.
- `bcrypt` is pinned to 4.0.1 (passlib breaks with 5.x). Don't upgrade.

## Hard constraints (violating these breaks the system)

- **Exactly 1 worker.** No `--workers`, no replicas: Telegram long polling allows one
  `getUpdates` connection per token.
- **Config comes from `.env` ONLY.** Shell environment variables are ignored entirely
  (`Settings.settings_customise_sources`). Docker overrides go through a mounted
  `.env.docker`. Adding a setting = edit `config.py` + `.env.example` + `.env`.
  `Langfuse`/Telegram keys: comment the line out to disable; empty string ≠ disabled.
- **All booking logic lives in `app/services/`.** Agent tools, REST routes, and the future
  Telegram bot are thin wrappers around services. If you write scheduling logic anywhere
  else, you've duplicated it.
- **Agent has NO create/write tool.** Flow: `propose_appointment` holds a slot; a separate
  `confirm` graph node creates the booking from DB values (never from model-typed strings).
  Adding a `create_appointment` tool removes the user's confirmation step — forbidden.
- **Only stream tokens tagged `respond`.** Time-parser LLM must use `tags=["timeparse"]` +
  `streaming=False`; supervisor output is routing JSON, never user-visible.
- **Context block goes first after system prompt and must state today's date** ("mai", "3h
  chiều" are unresolvable otherwise). It's per-turn, so it must NOT be inside the cached
  system prompt prefix.
- Mongo unique index on slots is **partial** (`{status: "booked"}`); cancel = flip `status`,
  never empty `slot_keys`. `DuplicateKeyError` subclasses `PyMongoError` — repositories
  convert it to `SlotTakenError` first.
- Authentication: refresh token is an opaque random string in an `HttpOnly` cookie (only its
  SHA-256 hash is stored); access token stays in memory client-side, never `localStorage`.
  Changing a password bumps `User.token_version` and deletes all refresh tokens.

## Layout

```
main.py                  entrypoint; lifespan init (Mongo fail-hard, LLM warn-and-continue)
app/services/            all business logic (appointment, auth, shop, conversation, rate_limit)
app/agents/booking_graph/  LangGraph: supervisor → respond/confirm nodes, 5 read-only tools
app/api/v1/              thin REST layer over services
app/repositories/        pymongo access; translates DuplicateKeyError → domain errors
scripts/seed_dev_users.py  idempotent; no public signup (admin creates users)
tests/                   pytest; markers: llm (real Azure), slow, integration
docs/superpowers/        specs + 4 plans; start at specs/2026-08-06-booking-nail-toc/README.md
```

## Gotchas

- LLM failure at startup is a **warning**, not an error — app runs, chat returns `error`
  events. Check logs before assuming the app is broken.
- `langfuse.langchain` needs the full `langchain` package, not just `langchain-core`;
  missing = traces silently off.
- Health check URL needs the trailing slash: `/api/v1/health/` (307 otherwise).
- `mongo-express` on :8081 (`admin`/`admin`) and Mongo exposed without auth are **dev-only**
  compose entries; prod split is planned (Plan 5), not done. Don't ship them as-is.
- Repo is Vietnamese-language throughout (docs, comments, UI strings), except commit messages — those are English and short. Match that.
- Plans 3 and 4 can run in parallel. After Plan 3: admin password reset via Telegram bot
  (`specs/2026-08-15-admin-password-reset-telegram-design.md`) — Zalo/SMS are permanently
  off the table, don't re-suggest them.
