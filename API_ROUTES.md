# API Routes Overview

Generated snapshot of main FastAPI application routes (excluding internal docs endpoints):

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | / | Landing HTML | - |
| GET | /api | Meta route list | - |
| GET | /health | Basic health | - |
| GET | /help | Help index/details | - |
| GET | /versions | Version history list | - |
| GET | /world/state | World JSON snapshot | - |
| POST | /chat | Chat + commands | optional (read) |
| GET | /chat/history | Chat history | - |
| GET | /chat/stream | SSE pseudo streaming | - |
| POST | /chat/to-proposal | Convert chat reply to proposal | write |
| GET | /analysis/json | Last analysis (structured) | - |
| POST | /analysis/inject | Inject suggestion as proposal | write |
| GET | /improve/json | Heuristic improve scan | - |
| POST | /improve/inject | Inject improve suggestion | write |
| GET | /proposals/pending | List pending proposals | - |
| GET | /proposals/preview/{pid} | Preview diff | - |
| POST | /proposals/apply | Apply proposal | write |
| POST | /proposals/undo | Undo last apply | write |
| POST | /apply/{proposal_id} | Legacy apply endpoint | write |
| POST | /chat/to-proposal | Duplicate entry (already listed) | write |

Twin / Sandbox (in sandbox app variant may differ):
| Method | Path | Purpose |
|--------|------|---------|
| POST | /twin/sandbox-cycle | Run sandbox evolution cycles |
| GET | /twin/changed | List changed sandbox files |
| POST | /twin/promote | Promote sandbox changes |
| POST | /twin/reset | Reset sandbox |
| POST | /snapshot/create | Create snapshot |
| GET | /snapshot/list | List snapshots |
| POST | /snapshot/restore/{id} | Restore snapshot |

Notes:
- Auth role mapping via API_TOKENS env (read/write/admin). Endpoints needing modification rights require at least write.
- Rate limiting global + per-IP in middleware; disable for tests using DISABLE_RATE_LIMIT=1.
- World state endpoint returns capped entity list (first 100).

Generated manually; update if new endpoints are introduced.
