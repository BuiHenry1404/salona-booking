# AGENTS.md

FastAPI + LangGraph booking app for a small nail/hair salon in Vietnam. Telegram bot in
`app/telegram/`, React frontend in `frontend/`. Live Socket.IO traffic runs on
`app/agents/booking/` (a fixed pipeline: resolve → orchestrate → brief → respond → record; the
old `booking_graph/` package is gone). The agent layer is about to be **rebuilt** on the
LangChain Subagents pattern — spec `docs/superpowers/specs/2026-11-08-subagents-rebuild-design.md`
(no plan yet). Old agent specs/plans were removed on 08/11; read them via git
(`git show 72c98fc:<path>`) only if you need history.

## Read first (in this order)

1. `CONTEXT.md` — the *why*: current state, locked decisions, already-fixed traps (do NOT
   "clean them up"), UI constraints for elderly users, security notes. Authoritative.
2. `RUNBOOK.md` — the *how*: setup, test accounts, Azure/Langfuse config, troubleshooting.
   Its agent/guard sections describe the current pipeline and will change with the rebuild.
3. The rebuild spec above before touching `app/agents/`. Work is executed TDD via the
   `superpowers` skills.

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
- **All booking logic lives in `app/services/`.** Agent tools, REST routes, and the
  Telegram bot are thin wrappers around services. If you write scheduling logic anywhere
  else, you've duplicated it.
- **Agent has NO create/write tool.** `propose_appointment` / `cancel_appointment` only
  create a *proposal* in the `proposals` collection (`ProposalService.create`). The
  customer agrees on a later turn (Đồng ý/Không button, or typed text that the
  Orchestrator labels via `decide_pending`; `orchestrator/decide.py::veto` may only
  downgrade that label, e.g. another time in the reply → `change`). No keyword
  dictionaries. Only `ProposalService.approve(user, proposal_id)` writes, and inside
  `app/agents/` only `decide.apply_decision` calls it — it re-checks owner, 10-min expiry,
  later turn, single use, and that a move/cancel target is unchanged. Adding a
  `create_appointment` tool (or HITL around one) removes the user's confirmation step —
  forbidden. `tests/test_write_fence.py` guards it.
- **Only user-visible tokens reach the screen.** Today only tokens tagged `respond`
  stream; every other model call is `streaming=False` with its own tag. After the
  rebuild nothing streams: the answer arrives in `complete` after the code guard.
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
app/agents/booking/      current pipeline (graph.py, orchestrator/, specialists/, guard); no tool writes; being rebuilt
app/api/v1/              thin REST layer over services
app/repositories/        pymongo access; translates DuplicateKeyError → domain errors
scripts/seed_dev_users.py  idempotent; no public signup (admin creates users)
tests/                   pytest; markers: llm (real Azure), slow, integration
docs/superpowers/specs/  product spec (2026-08-06-*), Telegram admin reset, the subagents rebuild spec
```

## Socket.IO protocol

Client → server events (`app/services/socketio_service.py`):

- `send_message {"message": str}` — a typed turn; rate-limited
  (`chat:user:<id>`, `settings.chat_max_per_hour`/hour).
- `confirm_proposal {"proposal_id": str, "decision": "yes"|"no", "message_id": str?}` —
  the Đồng ý/Không buttons on a pending proposal. **Not** rate-limited (the LLM cost
  was already spent proposing it); bad `decision`/missing `proposal_id` → `error`.
  `message_id` is accepted and ignored for now (dedupe lands in a later task).

Server → client events, both paths funnel through `run_turn` and emit the same
stream: `turn_started`, `tool_started`/`tool_finished`, `token` (only `respond`-tagged
tokens — see Hard constraints), then exactly one `complete {"answer": str,
"proposal": {"id", "kind", "expires_at", "when_spoken"} | None}` or `error {"message"}`.
`proposal` is non-`None` only when the turn ends on the `confirm` node's `interrupt()` —
the client should render the buttons then and clear them once `complete`/`error`
arrives without one.

## Gotchas

- Mongo runs as a single-node replica set (`rs0`, needed for transactions); every client URI
  needs `?directConnection=true` (`.env`, tests, scripts) or it hangs on server selection.
- LLM failure at startup is a **warning**, not an error — app runs, chat returns `error`
  events. Check logs before assuming the app is broken.
- `langfuse.langchain` needs the full `langchain` package, not just `langchain-core`;
  missing = traces silently off.
- Health check URL needs the trailing slash: `/api/v1/health/` (307 otherwise).
- `mongo-express` on :8081 (`admin`/`admin`) and Mongo exposed without auth are **dev-only**
  compose entries; prod split is planned (Plan 5), not done. Don't ship them as-is.
- Repo is Vietnamese-language throughout (docs, comments, UI strings), except commit messages — those are English and short. Match that.
- Admin password reset goes via the Telegram bot
  (`specs/2026-08-15-admin-password-reset-telegram-design.md`) — Zalo/SMS are permanently
  off the table, don't re-suggest them.
