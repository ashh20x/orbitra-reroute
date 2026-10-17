# Orbitra Reroute

AI agent that pre-reroutes refrigerated poultry shipments (Santos, Brazil → Jebel Ali, UAE) when Gulf / Hormuz / Red Sea disruption is detected. Every outbound message requires explicit human approval before sending.

**AMD Developer Hackathon Act III · Reinvent Commerce**

---

## What it does

1. **Polls Gmail** for disruption alerts from carriers
2. **Extracts** shipment details via Evolus Data Extractor
3. **Scores risk** — rules + ML classifier + Evolus route-risk agent (Gemma-4-31B-IT on AMD Token Factory)
4. **Builds provider workbook** — Jeddah and Salalah cold-chain providers, two-tab Excel
5. **Computes cost forecast** — P10 / P50 / P90 via Monte Carlo simulation
6. **Looks up 3D spatial world** for the recommended port (World Labs / Marble)
7. **Creates Gmail draft** — Trade Ops notification with workbook attached
8. **Human approval gate** — Trade Ops approves or rejects in the UI; nothing sends automatically
9. On approval: generates **provider outreach**, **Finance digest**, and **Compliance notes** drafts via Evolus AI Tools
10. Each downstream draft also queues in `/approvals` — every click is a deliberate decision

---

## Quick start

```bash
# 1. Clone and install
git clone <repo>
cd orbitra-reroute
uv sync

# 2. Configure
cp .env.example .env
# Fill in keys — see table below

# 3. Run
make dev          # http://localhost:8000

# 4. Send a fixture email to the demo inbox
uv run --env-file .env python scripts/seed_inbox.py --fixture a

# 5. In the UI: click "Check inbox now" on the Trade Ops Desk
#    or POST /inbox/check

# 6. Review draft at /approvals → Approve → draft sends to DEMO_SINK_EMAIL
```

### Docker

```bash
cp .env.example .env   # fill in keys
docker compose up --build
# → http://localhost:8000
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `TOKEN_FACTORY_API_KEY` | ✅ | AMD Token Factory key (`rc-…`) |
| `TOKEN_FACTORY_BASE_URL` | — | Default: `https://developer.amd.com.cn/radeon/api/v1` |
| `LLM_PRIMARY_MODEL` | — | Default: `Gemma-4-31B-IT` |
| `EVOLUS_API_KEY` | ✅ | Evolus workspace key (`evo_…`) |
| `EVOLUS_APP_ID` | ✅ | Evolus workspace Application ID |
| `EVOLUS_AGENT_ID` | ✅ | `route-risk` agent ID |
| `EVOLUS_EXTRACTOR_ID` | ✅ | `shipment-notice` extractor ID |
| `EVOLUS_WORKFLOW_ID` | ✅ | `pre-reroute-intake` automation ID |
| `EVOLUS_LIBRARY_ID` | ✅ | `OrbitraTeamMemory1` library ID |
| `GMAIL_REFRESH_TOKEN` | ✅ | OAuth refresh token — run `scripts/gmail_auth.py` |
| `GOOGLE_OAUTH_CLIENT_ID` | ✅ | Google Cloud OAuth 2.0 client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | ✅ | Google Cloud OAuth 2.0 client secret |
| `GMAIL_MONITOR_ADDRESS` | ✅ | Mailbox to poll |
| `DEMO_SINK_EMAIL` | ✅ | Catch-all for all outbound in demo mode |
| `DEMO_MODE` | — | `true` (default) — rewrites all recipients to `DEMO_SINK_EMAIL` |
| `RISK_THRESHOLD` | — | Default `Medium` — minimum level to trigger workflow |
| `PUBLIC_BASE_URL` | — | Default `http://localhost:8000` — set to Render URL on deploy |
| `FEATURE_SPATIAL` | — | `true` — enables World Labs spatial lookups |

See `.env.example` for the complete list.

---

## Make targets

```
make dev      # FastAPI dev server with .env hot-reload
make test     # pytest tests/
make smoke    # connectivity checks (Token Factory, Evolus, Gmail, spatial)
make seed     # send all 5 fixture emails to the demo inbox
make train    # train XGBoost / LSTM blockage classifier offline
make demo     # docker compose up --build
make lint     # ruff check
```

---

## Architecture

```
Gmail ──poll──▶ orchestrator
                     │
                     ├──▶ Evolus extractor (shipment-notice)
                     │         └── structured fields → DisruptionSignal
                     │
                     ├──▶ rules engine          (deterministic, no LLM)
                     ├──▶ ML classifier         (XGBoost / PyTorch ROCm — offline)
                     └──▶ Evolus route-risk agent
                               └── Gemma-4-31B-IT via AMD Token Factory
                                         │
                                   RiskAssessment
                                   (final_level, recommended_port)
                                         │
                          ┌──────────────┼──────────────┐
                    provider workbook  cost P10/P50/P90  W2 spatial lookup
                    (seed CSV / Places)  (Monte Carlo)   (Marble / World Labs)
                                         │
                               Gmail draft → /approvals
                                         │
                               Human APPROVE → send
                                         │
                          post-approval pipeline (AI Tools)
                          ├── provider outreach × 2
                          ├── Finance digest
                          └── Compliance notes
```

### Evolus building blocks (5)

| Block | Purpose |
|---|---|
| Document Library (`OrbitraTeamMemory1`) | Reroute procedure + port knowledge base |
| Specialized Agent (`route-risk`) | Risk scoring via Gemma on AMD Token Factory |
| Data Extractor (`shipment-notice`) | Structured extraction from disruption emails |
| Automation (`pre-reroute-intake`) | End-to-end flow: extract → assess → output |
| AI Tools (translate, summarize, email) | Portuguese translation, Finance digest, outreach drafts |

### AMD compute

- **Inference**: Gemma-4-31B-IT on AMD Token Factory (OpenAI-compatible)  
  Fallbacks: Qwen3.8-Flash-Next → DeepSeek-V4-Flash  
  Rate limiting: ≤ 4 concurrent, ~15 RPM token bucket, exponential backoff on 429
- **Training roadmap**: LSTM blockage classifier trainable on AMD ROCm GPU  
  (`make train` → `models/lstm_blockage.pt`)

---

## Demo scenarios

| Fixture | Email | Outcome |
|---|---|---|
| `a` | MV NORTHERN FROST — Hormuz diversion | **Critical** → Salalah |
| `b` | MV ATLANTIC ROSE — ETA slip +7 days | **High** → Salalah |
| `c` | MV ESTRELA DO SUL — Portuguese diversion | **High** (auto-translated) → Salalah |
| `d` | MV DUBAI STAR — Red Sea advisory | **Critical** → Salalah |
| `e` | Freight forwarder invoice (noise) | **Low** → `below_threshold`, no draft |

```bash
# Single fixture
uv run --env-file .env python scripts/seed_inbox.py --fixture a

# All fixtures
make seed
```

---

## Project layout

```
app/
  domain/cdm.py           Canonical Data Model
  orchestrator.py         State machine + post-approval pipeline
  llm.py                  AMD Token Factory client
  risk/
    rules.py              Deterministic keyword scoring
    assess.py             Hybrid: rules + ML + Evolus agent
    ml_model.py           XGBoost / LSTM inference
    feature_engineering.py  GDELT + PortWatch features (ML pipeline)
  integrations/
    gmail.py              Poll, draft, send, delete, mark-read
    evolus.py             Workflow, extractor, agent chat, AI Tools
    places.py             Google Places provider qualification
    worldlabs.py          W4 on-demand world generation
    gdelt.py              GDELT news + Goldstein score
    portwatch.py          IMF PortWatch vessel data
  workbook/build.py       Two-tab Excel provider workbook
  cost/model.py           Monte Carlo P10/P50/P90 cost forecast
  spatial/lookup.py       World Labs world lookup (config/worlds.json)
  web/templates/          Jinja2 UI (Trade Ops Desk, Approvals, Situation Room)
  store.py                SQLite persistence
  main.py                 FastAPI app

config/worlds.json        W1 Marble world URLs (World Labs teammate)
data/providers_seed.csv   10 cold-chain providers (Jeddah + Salalah)
scripts/                  Smoke tests, seed inbox, Gmail auth, training
fixtures/emails/          5 synthetic disruption emails
```

---

## Guardrails

- `DEMO_MODE=true` rewrites **all** recipients to `DEMO_SINK_EMAIL` — no real emails ever sent
- Synthetic vessel names and B/L numbers only
- Every outbound message requires a human approval click
- Cost figures always labelled "Estimate pending actual quotes"
- AI transparency line on every draft
- World Labs label: "AI-generated spatial approximation — verify with provider / port authority."
- Secrets never committed — `.env` git-ignored, pre-commit gitleaks hook active

---

## Tracks

- **Reinvent Commerce** (primary) — service quality, margin impact, inventory availability
- **Evolus Business Agents on AMD** (partner) — 5 building blocks driven by API
- **Gemma / Google prize** — Gemma-4-31B-IT as primary inference model

---

*AMD Developer Hackathon Act III · Deadline 18 Oct 2026 14:00 GST*
