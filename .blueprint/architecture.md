# Architecture

## Boundary dan Otoritas

| Komponen | Tanggung jawab |
|---|---|
| FastAPI | Auth, ownership, katalog/plan resolver, invoice Xendit, wallet ledger, reservasi, credential broker, CRUD/state, room admission, signed media, outbox |
| Realtime worker | LiveKit media, VAD/STT, streaming LLM langsung, frame vision, ElevenLabs, turn cancellation, podcast director, usage checkpoints |
| Langflow | Chat/Learn reasoning, RAG, PDF/chunk/script, ekstraksi fakta, assessment, TOEFL subjektif, dua embedding projection, background orchestration AI |
| CallCraft | Registry/schema, routing dan eksekusi seluruh tool/function call dari Langflow maupun realtime worker |
| PostgreSQL | Otoritas account, pesan, fakta, score, canonical chunks, ledger, payment, registry, job dan outbox |
| Astra DB | Projection vektor terpisah per provider/model/version/scope; bukan sumber saldo/fakta otoritatif |
| Durable worker + Redis Streams | Outbox dispatch, retry/dead letter, schedule, reconciliation; SQL job tetap sumber state |
| S3-compatible | Paper privat, voice note, audio segment podcast opsional; tanpa binary di SQL |
| LiveKit / ElevenLabs / Xendit / Google | Media transport / TTS platform / pembayaran / identitas federasi |

```text
Client -> FastAPI -> PostgreSQL + outbox -> Redis -> worker -> Langflow workflows
             |                           |                  |-> Gemini embedding (admin)
             |-> Xendit <-> webhook       |                  |-> OpenAI embedding (admin)
             |-> credential broker       |                  +-> Astra projections
             +-> Langflow chat/Learn -> CallCraft -> internal domain API

Client <-> LiveKit <-> realtime worker -> STT -> LLM -> ElevenLabs
                              |            (direct streaming, no Langflow turn)
                              |-> CallCraft -> internal domain API
                              +-> persisted turns/usage/outbox -> background Langflow
```

## AI Runtime Snapshot

Backend resolves `plan_revision`, agent/persona version, LLM/STT model and credential refs, base URL policy, embedding profile, flow/prompt version, voice mapping, rate card and budgets before operation. Snapshot is immutable for a job/call/playback. Secrets are resolved just-in-time, never stored in the snapshot. Revocation dan model emergency-disable still cancel new invocations within running sessions.

VIP resolves provider credentials from platform environment/secret manager. Advance resolves encrypted user credentials separately for LLM/STT. Background maintenance uses platform model/key; it must not unexpectedly consume BYOK or wallet after the foreground action. User-requested podcast generation and subjective TOEFL evaluation do use the job's selected plan. Allocation details: `billing-plans.md`.

## Langflow-centric Dengan Jalur Realtime Khusus

Langflow owns versioned non-realtime AI workflows, not just one giant flow. Realtime worker loads published persona/prompt/config artifacts validated during deployment; it never needs a Langflow run for every utterance. Context preparation/retrieval can occur before admission and via asynchronous refresh. If direct realtime retrieval is needed, use a bounded shared retrieval adapter with identical Astra scope policy and admin query embeddings. No unbounded retrieval or memory writes in audio hot path.

CallCraft remains the only execution route for AI-selected functions. Deterministic trusted service operations (persist message, reserve wallet, fetch authorized context, dispatch job) are not LLM function calls. They use narrow backend APIs/ports rather than pretending to be agent tools.

## Identity, Delivery, dan Secret

- Backend-issued access token resolves principal; Google identity is verified at login, not passed around as app authorization.
- Per-execution signed context includes issuer, audience, user, resource, purpose, permitted tools, expiry, request/trace ID. Prompt/client cannot override it.
- Credential broker issues one-use, audience/purpose-bound references. Internal Langflow component or trusted realtime adapter resolves key over authenticated TLS. Delayed jobs obtain fresh references after reauthorization, not expired tokens copied from queue.
- Langflow run storage, traces and component diagnostics must demonstrably redact/exclude credentials; otherwise BYOK activation is blocked.
- Domain mutation + outbox commit atomically. Worker acknowledgment follows durable result. Duplicate delivery uses event/job keys; provider requests additionally require invocation identity and reconciliation.
- Persist input before invocation, output/status/usage afterward. Audio processing uses bounded persistence queues and periodic checkpoints; if durable storage/backpressure fails beyond configured budget, stop session explicitly rather than losing history/billing silently.

## Failure Semantics

No hidden provider/model/credential fallback. Dependency timeout returns typed failure with correlation. A partial answer records actual delivered/consumed usage and terminal state. Langflow ingestion outages accumulate durable work without delaying healthy realtime turns. Billing authorization and required credential/context must succeed before work starts. Kill switch blocks new operations and gracefully terminates affected work; refunds/settlement still run.

Data deletion tombstones canonical sources before vector/media deletion. Background workers check tombstones and source versions so delayed jobs cannot resurrect removed data. Tenant scope is single-user ownership, with admin-shared published agent/learning knowledge explicitly separate.
