# API dan Event Contracts v3

Ini resource/behavior contract untuk tahap desain. Pydantic/OpenAPI/JSON schemas dibuat sebelum handler pada fase coding. Public prefix `/v1`; private resources selalu owner-scoped. Cursor pagination, explicit maximum, request/trace correlation, optimistic version untuk update dan idempotency untuk mutasi wajib.

## Public API

| Area | Endpoints dan minimum payload/result |
|---|---|
| Health | `GET /health/live`, `/health/ready`; feature readiness bukan raw secret diagnostics |
| Auth | `POST /v1/auth/register` email/password, `/login`, `/email:verify`, `/email:resend`, `/password:forgot`, `/password:reset`, `/refresh`, `/logout`, `/logout-all` |
| Google | `GET /v1/auth/google/start`, `/callback`; `POST /v1/me/identities/google:link`, `DELETE /v1/me/identities/google`; browser transaction/reauth binding |
| Profile | `GET/PATCH /v1/me/profile`, `GET /v1/me/progress`, `/assessments`, `/facts`; `PATCH/DELETE /v1/me/facts/{id}`, `DELETE /v1/me` starts deletion job |
| Plan | `GET /v1/plans`, `GET/PUT /v1/me/plan` plan_code + expected_revision |
| Billing | `GET /v1/token-packages`, `/v1/rate-cards/current`, `/v1/me/wallet`, `/v1/me/wallet/ledger`; `POST /v1/billing/topups` package_version_id; `GET /v1/billing/topups/{id}`; `POST /v1/ai/quotes` feature/model/input refs + max spend |
| AI settings | `GET /v1/ai/providers`, `/v1/ai/models?capability=...`; `POST /v1/me/ai-credentials`; `POST .../{id}:verify`; `GET/DELETE .../{id}`; `PUT /v1/me/ai-selections/{llm|stt}` model_id + credential_id |
| Agents | `GET /v1/agents`, `/v1/agents/{id}` public metadata/voice label, no prompts/secrets |
| Chat | `GET /v1/practice/categories`; `POST/GET /v1/practice/sessions`; `GET .../{id}`; `POST .../{id}/messages` text or media_ref, client_message_id; `POST .../{id}:complete`; session create agent_id/category_id |
| Vocabulary | `GET/POST /v1/vocabulary`, `GET/PATCH/DELETE /v1/vocabulary/{id}`, `POST .../{id}/reviews` |
| Home | `GET /v1/courses`, `/v1/lessons/{id}`; `PUT /v1/learning-progress/{contentVersionId}`; `POST /v1/learn/assist` |
| Media | `POST /v1/media/uploads` purpose/size/checksum; `POST /v1/media/{id}:complete`; `GET /v1/media/{id}/download-url`; signed scoped URLs |
| TOEFL / Profile | `GET /v1/toefl/tests`; `POST/GET /v1/toefl/attempts`; `PUT .../{id}/submissions/{questionRef}`; `POST .../{id}:submit`; `GET .../{id}` |
| Call | `POST /v1/calls` mode voice/video + agent_id + budget; `POST .../{id}:join-token`, `.../{id}:end`; `GET .../{id}` |
| Podcast | `POST/GET /v1/podcasts`; `GET/PATCH/DELETE .../{id}`; `POST .../{id}/sources` media_id; `POST .../{id}:generate`, `.../{id}:regenerate` target_duration + budget; `GET .../{id}/segments` |
| Playback | `POST /v1/podcasts/{id}/playbacks` script_version_id + budget; `GET /v1/podcast-playbacks/{id}`; `POST .../{id}:join-token`, `.../{id}:end` |
| Jobs | `GET /v1/jobs/{id}`, `POST .../{id}:cancel`, `POST .../{id}:retry`; retry checks ownership, stage checkpoint, quote/reserve and credential validity |

Async generation/verification/evaluation returns 202 with job ID/status URL; ready/result is never asserted on acceptance. Creates 201; successful mutation 200/204. Auth register/reset use generic accepted response to avoid enumeration. Idempotency key required for domain POST mutations; OAuth callback/vendor webhook use protocol-specific replay protection, token-consuming auth routes use single-use token semantics. PUT/PATCH use expected version. Same key/different hash → 409.

Error envelope: `{"error":{"code":"INSUFFICIENT_TOKENS","message":"Saldo token tidak cukup.","request_id":"...","details":[]}}`. Status: validation/capability 422, auth 401, permission 403, private resource missing 404, conflict/plan busy 409, insufficient balance 402, payload 413, rate/capacity 429, provider invalid contract 502, unavailable 503. BYOK/model/embedding failures use explicit stable codes, not raw vendor responses.

## Webhooks dan Internal APIs

- `POST /v1/webhooks/xendit`: callback auth + provider confirmation + durable inbox; reference/amount/currency validation; no user JWT. `POST /v1/webhooks/livekit`: vendor signature verification, dedupe, room ownership mapping. Limit body size and redact payload secrets.
- `/internal/v1/tools/*`: CallCraft service identity + signed execution context, tool-specific scope/idempotency. Domain auth remains authoritative.
- `POST /internal/v1/flow-data/{conversation|toefl-attempt|podcast-source|learning-content|agent-knowledge|canonical-chunks}`: scoped resource refs, no arbitrary SQL/filter.
- `POST /internal/v1/credentials:resolve`: one-use audience-bound reference, trusted Langflow/realtime service + execution context; never public client.
- `/internal/v1/runtime/{canonical-documents|projection-results|usage-checkpoints|session-checkpoints}`: narrow trusted component persistence, not AI-callable tools. Idempotent schema/version and ownership/source-version fencing mandatory.

## Domain Events

| Event suffix `.v1` | Producer → consumer |
|---|---|
| `conversation.range_recorded`, `conversation.session_completed` | Message/turn commit → ingestion, facts, assessment; includes chat/call/podcast |
| `learning.content_published`, `agent.knowledge_published` | Published immutable version → canonical ingestion |
| `knowledge.canonical_committed` | Canonical source commit → dual embedding dispatch |
| `embedding.projection_completed`, `embedding.projection_failed` | Branch result → aggregate/reconcile readiness |
| `podcast.source_uploaded` | Scanned finalized media → document ingestion |
| `podcast.source_processed` | Canonical source → generation when requested/authorized |
| `podcast.script_ready`, `podcast.playback_completed` | Script/session commit → UI status, final learning consolidation |
| `toefl.attempt_submitted`, `toefl.score_recorded` | Submit → evaluation; score → feedback ingestion |
| `payment.webhook_received`, `payment.topup_paid` | Inbox → reconciliation; ledger commit → notification |
| `usage.reconciliation_requested`, `wallet.balance_changed` | Meter/ledger → reconciliation and user status |
| `auth.email_requested`, `user.deletion_requested` | Durable mail dispatch; multi-store deletion |

Envelope: event_id, event_type/version, aggregate_type/id/version, occurred_at, trace_id, minimal reference payload. No keys/tokens or raw paper/audio. Outbox publish at-least-once; consumers dedupe and reload current authorized canonical records. Event ordering uses aggregate version, not arrival time.

## Streaming / LiveKit Data

Chat SSE events: `started`, `text_delta`, `tool_started/completed`, `usage_updated`, terminal exactly one of `completed|cancelled|failed`. IDs include request/message/session, sequence, schema_version. Reconnect via Last-Event-ID never restarts provider/tool invocation; expired replay fetches persisted state.

LiveKit data: `session.state`, `turn.state`, `transcript.partial/final`, `speaker.changed`, `playback.position`, `interruption.accepted`, `balance.low/exhausted`, `session.closing/ended`, `error`. Include session_id, sequence, epoch, timestamp, typed payload. Data channel is transient; reconnect fetches authoritative HTTP state and delivered transcript checkpoint. Client-supplied transcript, price, usage or completion never become financial truth.
