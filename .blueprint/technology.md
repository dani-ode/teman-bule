# Technology dan Dependency Preparation

Target modular monolith Python 3.12: satu codebase dengan proses API, durable worker dan realtime worker terpisah. Langflow/CallCraft/Astra/LiveKit/ElevenLabs/Xendit/Google/S3 adalah external services. Dependency manifests yang ada adalah baseline lama; perubahan/install/lock dilakukan setelah contract spikes pada fase coding, bukan bagian perubahan spec ini.

| Stack | Tanggung jawab / persiapan |
|---|---|
| FastAPI + Uvicorn | Public/internal API, SSE, schema validation; bukan realtime media server |
| Pydantic + pydantic-settings | Typed schemas, secret redaction, conditional feature config validation |
| PostgreSQL 16 + SQLAlchemy async + asyncpg + Alembic | Domain truth, ledger/locking, immutable versions, outbox, migrations |
| Redis 7 Streams | Delivery/consumer groups/retry; AOF, SQL reconciliation; bukan financial truth |
| HTTPX | Typed adapters Langflow/CallCraft/Xendit/provider HTTP, pooling/deadlines; no blind write retries |
| PyJWT[crypto], cryptography | App JWT + execution context, Google ID token verify, envelope encryption/KMS |
| Argon2id library, OAuth client library | Password hashing dan Google authorization-code/PKCE; evaluate `argon2-cffi`/`Authlib` saat resolution |
| SMTP client | Durable verification/reset mail; explicit TLS mode, no logging token/link |
| Langflow | Multiple workflows; custom components under `custom_langflow_components/`; exports/prompts version-controlled |
| CallCraft API/MCP | All AI function execution, specs in `custom_callcraft_spec/`; versioned real contract |
| Astra + astrapy | Separate Gemini/OpenAI spaces; provisioning/reconciliation/direct bounded realtime retrieval adapter |
| Gemini/OpenAI adapters | LLM/STT capability selection, admin dual embedding; SDK versions verified, no arbitrary vendor fallback |
| LiveKit API/Agents | Room/token/dispatch/audio/video, one podcast director; worker separate process |
| ElevenLabs adapter/plugin | Platform TTS, Elean/Willy distinct voices; no user debit |
| Xendit adapter | Hosted checkout/payment product selected in Phase 0; webhook verification, provider reconciliation, refunds |
| boto3 / S3 | Private PDF/media/cached segments; blocking SDK calls isolated from async/audio loop |
| PDF parser + malware scanner | Sandboxed limits, page citations, scanned PDF explicit unsupported until OCR agreed |
| OpenTelemetry | End-to-end traces/metrics, redacted provider usage/cost, no prompt/key capture |
| Apache + Docker Compose | Host TLS/reverse proxy, private SQL/Redis, independently scaled workers |
| uv, pytest, pytest-asyncio, testcontainers, ruff, mypy, schemathesis | Reproducible dependency resolution, database/contract/security tests |

## Planned Layout

Layout tunggal mengikuti `backend-layout.md`: `src/temanbule/{api,worker,realtime,platform,modules}`. Setiap modul memiliki domain/application/infrastructure/contracts sesuai kebutuhan; domain tidak mengimpor SDK provider/framework. Adapter vendor berada pada infrastructure modul pemilik, sedangkan plumbing bersama berada pada platform. Versioned prompts/flow exports under future `langflow/`; realtime references same published persona artifacts. Tests split unit/integration/contract/e2e/evaluation.

Provider SDKs and LiveKit plugins pinned only after verified availability/compatibility. `requirements.txt` direct compatible ranges → resolver-generated `requirements.lock` with hashes; no production floating install. Custom Langflow components may need a separate lock matching deployed Langflow runtime. Library version or API shape must not be invented in blueprint.

Config groups and required validation are defined in `environment.md`. Runtime catalog/pricing/prompt/collection activation reside in DB registries, not hardcoded constants. Env provides bootstrap coordinates and secret references; drift from activated registry must fail validation rather than override historical snapshots.
