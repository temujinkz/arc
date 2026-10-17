# ARC

ARC is a workspace for public professional research.
Collect source evidence, inspect its provenance, and explore a sourced profile through conversation, dossiers, and graphs.
The application combines a FastAPI backend, a Next.js frontend, SQLite history and source caching, and optional Neo4j graph storage.

## Features

- An evidence workbench for explicit public web, username, publication, DNS, certificate, and archive lookups.
- Saved source payloads with collection times, SHA-256 integrity checks, request IDs, and source URLs.
- Model-assisted investigations with live activity, human questions, cancellation, and evidence-only follow-ups.
- Professional portraits and graph views with citations and assessment limitations.
- The Council: evidence-linked readings through Jung, Diogenes, Nietzsche and a fictional Builder, with saved comparisons and explicit model starts.
- Separate peer claims and a private local journal with explicit approval before sharing context.
- Evidence JSON, Markdown reports, and Maltego CSV exports.

Source records and model assessments remain separate.
Matching handles do not establish identity, and repeated snapshots of the same source do not count as independent corroboration.

## Requirements

- Python 3.13 or newer and [uv](https://docs.astral.sh/uv/).
- Node.js and npm for the frontend and optional MCP integrations.
- Optional provider credentials for model-assisted research and paid source tools.
- Optional Neo4j credentials for public graph storage.

Dependency versions are pinned in `uv.lock` and `frontend/package-lock.json`.

## Local setup

Install the Python dependencies:

```sh
uv sync --locked
```

Create an ignored `.env` using `.env.example` as a reference.
Keep existing credentials private and configure only the integrations you intend to use.
Opening the application or creating a free evidence case does not start a paid investigation.

Start the source MCP server:

```sh
uv run --locked --env-file .env python -m backend.mcp_servers.osint_mcp
```

In another terminal, start the API:

```sh
uv run --locked --env-file .env uvicorn backend.main:app --host 127.0.0.1 --port 8010
```

Start the frontend in a third terminal:

```sh
cd frontend
npm ci
NEXT_PUBLIC_API_URL=http://127.0.0.1:8010 npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open [ARC](http://127.0.0.1:3000).
The API runs on [port 8010](http://127.0.0.1:8010/docs), and the MCP server uses `http://127.0.0.1:8001/mcp`.
Keep the API on loopback because profile ownership and private operations are designed for a local device.

To retain the frontend API setting, place `NEXT_PUBLIC_API_URL=http://127.0.0.1:8010` in `frontend/.env.local`.
This value is public frontend configuration; provider credentials belong only in the backend `.env`.
`NEXT_PUBLIC_API_URL` must be set when building the frontend.

## Optional local OSINT tools

Sherlock, Maigret and theHarvester are optional. `scripts/install-osint-tools.sh` installs pinned versions under `.arc/tools` and links them into `.arc/bin`, which the backend checks first.
`/api/tools` shows which are installed.

## Use the workspace

Choose **Evidence** or **Or start with free source lookups** to create an evidence case without invoking a model.
Select a source, provide its public query or identifier, and explicitly submit each lookup.
The ledger displays captured payloads, provenance, coverage, source health, and available report exports.
Local adapters such as Sherlock, Maigret, and theHarvester require separate installations.
Their installation status is available through `GET /api/tools` and the source catalog.
The adapter modules describe accepted executable paths and report formats.

The **Conversation** workflow creates a real provider-backed investigation.
Configure a positive `CLAUDE_MAX_BUDGET_USD`, the required Neo4j settings, and the selected provider credentials before starting one.
The configured investigation allowance includes recorded Claude and optional Crusoe model usage and follow-ups; it is not a prepaid hard cap and excludes source-provider charges.
`APIFY_MAX_TOTAL_CHARGE_USD` controls actor event charges and does not cover separate platform or storage charges.
Use the configured model selection and per-role overrides rather than changing models implicitly.

Completed runs replay their saved events without launching another investigation.
Interrupted or unknown provider operations are not automatically resumed or retried.
A fresh source snapshot requires an explicit request.

## Recursive research and Crusoe workers

The normal Investigate action collects sources through the Claude Agent SDK,
then extracts focused claims into a SQLite case. Workers propose findings with
exact passage IDs; the critic reviews support and may queue follow-up work.
The scheduler allows at most three waves, depth two and eight tasks. Reviewed
findings produce the portrait and Neo4j graph. Dossier and Graph inspectors show
verbatim passages, source URLs, collection dates and support status. A completed
run can legitimately stop before three waves when no useful tasks remain.

To use Crusoe for focused public-evidence workers, configure the ignored `.env`:

```dotenv
ARC_RESEARCH_WORKER_PROVIDER=crusoe
CRUSOE_API_KEY=<your inference key>
CRUSOE_WORKER_MODEL=nvidia/NVIDIA-Nemotron-3.5-Super-VL
```

Restart the API after changing its environment. Claude retains SDK/MCP harvesting,
claim extraction, critic review and portrait generation. Private journal routing
is unchanged; neither worker packets nor Crusoe receive private notes.
`ClaudeSDKClient` is never pointed at an OpenAI-compatible endpoint.

Preflight checks the authenticated `/v1/models` catalog for the exact model ID,
JSON output support, output limits and prices before paid harvesting starts.
The default Crusoe base URL is `https://api.inference.crusoecloud.com/v1` and the
application output bound is 4,096 tokens. Admission reserves catalog full-context
costs, so it can reject large-context models even for small actual packets.
There is no automatic paid retry or paid-provider fallback; unknown usage blocks
further spending. Credits and actual billing are account-specific.

You.com (`YDC_API_KEY`) supplies paid web/news discovery through
`mcp__osint__search_web_you`. Exa is no longer registered or required. Existing
free lookups remain available. Search snippets and matching usernames are leads,
not independent identity verification. Tool errors remain visible in the stream.

Official sources checked October 18, 2026:

- [Crusoe inference documentation](https://docs.crusoecloud.com/serverless-inference/)
- [Model capabilities catalog](https://support.crusoecloud.com/hc/en-us/articles/55002524169499-How-To-List-Model-Features-via-curl-and-the-Inference-API)
- [Crusoe pricing](https://www.crusoe.ai/cloud/pricing)

The configured Nemotron model passed one authorized live worker check. A separate
authorized full hybrid run completed API/SSE, two Crusoe workers, critic/portrait,
Neo4j commit and browser dossier/graph/passage/reload checks. Read-back matched
six nodes and four edges; one of five portrait sections had insufficient evidence.
The run used one wave, with 26 captured source results and nine failed calls.
Recorded model usage cost $0.18536866, excluding search/scraping charges.
Receipts and source payloads remain in ignored local storage, not this repository.

Known limitations from that run: initial passage selection omitted useful
collected primary articles, limiting the final portrait; long research requests
render verbatim as profile titles. A successful graph commit does not establish
complete coverage or independently verify every claim. The run did not validate
multi-wave live recursion or every source tool. The no-credit saved-profile action
requires the original local saved session/artifacts; it is hidden when unavailable.

## The Council

After collecting evidence, open **The Council**, select one to four voices, and choose **Let them judge**.
That action authorizes one model reading using the configured orchestrator model and remaining investigation threshold.
Historical voices are inspired lenses, not authentic quotations or psychological diagnoses.
Each take includes its supporting evidence, caveats and a question to explore; insufficient evidence can produce a withheld judgment.
Readings remain separate from source facts, public graphs, peer claims and private journal context.
Saved readings survive restarts and retain their original evidence snapshot. New sources do not automatically trigger another model call.
Evidence cases can use the configured positive `CLAUDE_MAX_BUDGET_USD` threshold; each reading's reported usage reduces the remainder.

## Storage and privacy

Research history and source caching use `.arc/arc.sqlite3` by default.
`ARC_DATABASE_PATH` can override that location.
A single API worker owns active runtime tasks and human questions.

Private journal entries, ownership tokens, and visitor inbox data use a separate `.arc/private/vault.sqlite3` database.
`ARC_PRIVATE_VAULT_PATH` can override the private vault location, which must remain separate from research storage.
The vault is local to the machine running the API and is not encrypted at rest.
Owner cookies provide local access; global identity verification and account recovery are not implemented.

Journal edits invalidate approval of shareable context.
Visitor responses use public facts and explicitly approved statements; raw journal notes do not enter public graphs, research history, or visitor prompts.
Optional local inference uses `ARC_LOCAL_AGENT_URL` and `ARC_LOCAL_MODEL` with no cloud fallback.
The local model is optional. Research, the graph and the site work without it.
When local inference is unavailable, the interface offers manual context or an approved static brief.

Credentials, databases, local tools, verification receipts, and dependency directories are ignored by Git.
Preserve original evidence collection times when reusing saved source results.

## API entry points

| Route | Purpose |
| --- | --- |
| `GET /api/investigations` | List saved investigations and evidence cases |
| `POST /api/cases` | Create a free evidence case |
| `POST /api/investigations` | Start a configured model-assisted investigation |
| `GET /api/investigate/stream/{session_id}` | Replay and stream investigation events |
| `GET /api/investigate/{session_id}/ledger` | Inspect source records and assessment links |
| `POST /api/investigate/{session_id}/sources` | Submit an explicit free source lookup |
| `GET /api/judgments/personas` | List Council personas without invoking a model |
| `GET /api/investigate/{session_id}/judgments` | Inspect saved readings, evidence snapshot and budget availability |
| `POST /api/investigate/{session_id}/judgments` | Explicitly authorize an idempotent persona reading |
| `GET /api/investigate/{session_id}/exports/report.json` | Export evidence and the saved assessment |
| `GET /api/investigate/{session_id}/exports/report.md` | Export a readable evidence report |
| `GET /api/investigate/{session_id}/exports/maltego.csv` | Export a committed public graph |

The complete request schemas are available in the API's [OpenAPI documentation](http://127.0.0.1:8010/docs).

## Checks

Run the offline backend regressions:

```sh
uv run --locked python -m unittest discover -s backend/tests -v
```

Check and build the frontend:

```sh
cd frontend
npm run lint
NEXT_PUBLIC_API_URL=http://127.0.0.1:8010 npm run build
npm start -- --hostname 127.0.0.1 --port 3000
```

The offline tests cover persistence, source caching, evidence integrity, request idempotency, interrupted operations, cost accounting, and private representation boundaries.
They do not invoke live models or paid providers.
Live provider workflows require separate authorization and configured budgets.

## Project layout

- `backend/main.py` and route modules expose the API and investigation lifecycle.
- `backend/orchestrator.py`, `agent.py`, and `conversation.py` handle model sessions and MCP integrations.
- `backend/mcp_servers/`, `osint_tools.py`, and `domain_sources.py` implement source tools.
- `backend/scratchpad.py`, `evidence_ledger.py`, `source_cache.py`, and `storage.py` preserve evidence and history.
- `backend/graph_*.py`, `portrait.py`, and `public_profiles.py` manage sourced public graphs and portraits.
- `backend/private_vault.py` and the local-agent modules isolate private context and approved representation.
- `frontend/app/`, `components/`, and `lib/` implement the workspace, public profiles, and API/event state.
- `backend/tests/` contains the offline regression suite.
