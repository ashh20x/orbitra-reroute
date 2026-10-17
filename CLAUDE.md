# Orbitra Reroute — Project Memory

## What we are building
An AI agent that runs the pre-re-route process for refrigerated poultry shipments (Santos, Brazil → Jebel Ali, UAE)
when Gulf/Hormuz/Red Sea disruption is detected. Integrates Gmail, Evolus (5 building blocks), AMD Token Factory
(Gemma-4-31B-IT inference), a GDELT + PortWatch ML blockage classifier, Google Places provider qualification,
and a World Labs spatial layer. Every outbound message requires explicit human approval before sending.

**Hackathon:** AMD Developer Hackathon Act III · Deadline 18 Oct 2026 14:00 GST (10:00 UTC)
**Tracks:** Reinvent Commerce + Evolus Business Agents on AMD (partner) + Gemma/Google prize

## Commands
```bash
make dev          # uvicorn app.main:app --reload
make test         # pytest tests/
make smoke        # runs all smoke_*.py scripts; skips cleanly if keys missing
make seed         # python scripts/seed_inbox.py --all
make demo         # docker compose up --build
make train        # python scripts/train_model.py  (ML blockage classifier)
```

## Environment variables (see .env.example for full list)
| Var | Purpose |
|---|---|
| `TOKEN_FACTORY_BASE_URL` | `https://developer.amd.com.cn/radeon/api/v1` |
| `TOKEN_FACTORY_API_KEY` | `rc-` + 48 hex — team key, shared |
| `LLM_PRIMARY_MODEL` | `Gemma-4-31B-IT` |
| `LLM_FALLBACK_MODELS` | `Qwen3.8-Flash-Next,DeepSeek-V4-Flash` |
| `EVOLUS_API_KEY` | `evo_...` |
| `EVOLUS_APP_ID` | workspace UUID |
| `DEMO_MODE` | `true` — rewrites all recipient addresses to DEMO_SINK_EMAIL |
| `RISK_THRESHOLD` | `Medium` — minimum level to trigger workbook + notification |

## AMD Token Factory gotchas (§4)
- Base URL: `https://developer.amd.com.cn/radeon/api/v1` (OpenAI-compatible)
- **Limits:** 30 RPM / 8 concurrent per key; 20 RPM per account (60 s sliding window)
- **429 only:** retry with exponential backoff + jitter; honour `Retry-After`
- **Silently dropped params:** `stop`, `seed`, `top_k`, `logit_bias`; `thinking:{...}` returns 400
- **Structured output:** use `response_format: {"type": "json_object"}` — NOT strict `json_schema`
- **Empty content + finish_reason=length** means `max_tokens` was too low
- System message must be first in the messages array
- `MinerU2.5-Pro` OCR at `POST /v1/ocr` (optional)
- Fail loudly at startup if Gemma is missing from `GET /v1/models`
- **Trial/dev only:** no sensitive personal, health or financial data; use synthetic shipment data only

## Evolus gotchas (§5)
- REST base: `https://api.evolus.ai/api/v1` — headers `X-ApiKey` + `X-Application-Id`
- Agent chat base: `https://api.evolus.ai/v1` — `Authorization: Bearer <key>`, model `<appId>:<agentId>`
- Workflow execute: `POST /workflows/{id}/execute` → `202` + `executionId`; poll or use `callbackUrl`
- A workflow that is **off** returns `409` — always check workflow is enabled
- **NEVER send secrets as workflow parameters** (stored with the run)
- Extractor sync: add `?useWebhook=false`
- Library language: always pass `language=en` (default is Italian)
- Rate: 60 req/min per workspace; `402` = credits exhausted (50k available)
- Send back `X-Conversation-Id` to continue an agent conversation

## World Labs spatial rules (§10)
- Exact label everywhere the asset appears: **"AI-generated spatial approximation — verify with provider / port authority."**
- Never block the Trade Ops notification — if no world, notification goes without link
- W2 (cached lookup) is a dict lookup from `config/worlds.json`; W4 (on-demand API) is behind `FEATURE_WORLDLABS_API`
- Cache aggressively; never generate live during the demo
- Test every share link in a logged-out browser before demo

## Guardrails — non-negotiable
- Never commit secrets. `.env` is git-ignored. Pre-commit gitleaks hook is active.
- `DEMO_MODE=true` rewrites ALL outbound recipients to `DEMO_SINK_EMAIL` — nothing emails real providers
- Synthetic shipment data only (fictional vessel names and B/L numbers)
- Never invent provider details, review scores, regulatory requirements, or cost rates
- Cost numbers always labelled as estimates pending actual quotes
- Every outbound message requires a human approval click before `drafts.send`
- No stub may pretend to work: unimplemented pieces raise `NotImplementedError` with a TODO
- Never misreport which model produced an output
- AI transparency line on every outbound: "Drafted by an AI agent; reviewed and approved by {approver}."

## Definition of done per phase
- **P0:** `make smoke` exits 0 for every script whose key is present
- **P1:** fixture (a) → approval → sink email received; risk card + workbook + spatial link all present
- **P2:** cost P10/P50/P90 chart; Finance + Compliance drafts; AMD panel live
- **P3:** scenarios (b)–(e) correct; situation room changes between ports
- **P4:** `docker compose up` from clean clone → all scenarios → lablab submission live

## ML pipeline (Ericka's workstream)
- `app/risk/feature_engineering.py` — GDELT + PortWatch → `ChokePointFeatures`
- `app/risk/ml_model.py` — XGBoost inference (default); LSTM/PyTorch path is a stub
- `scripts/train_model.py` — offline training on GDELT + `data/portwatch_targets.csv`
- Saved artifact: `models/blockage_classifier.pkl` (XGBoost) or `models/lstm_blockage.pt` (PyTorch/ROCm)
- Interface: `predict_blockage(features: ChokePointFeatures) -> BlockagePrediction`
- **ML model choice (XGBoost vs LSTM) deferred — decide before P1 ends**
- LSTM recommendation: include code + training script; demo as AMD ROCm roadmap if no GPU available
