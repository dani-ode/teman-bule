# PostgreSQL Schema v3

SQL adalah sumber kebenaran. `snake_case`, `timestamptz`, ULID `varchar(26)` tervalidasi, FK eksplisit, integer untuk uang/token, JSONB hanya payload versioned. Mutable rows: `created_at`, `updated_at`, optimistic `version`; immutable rows append-only. Semua FK query path punya index terukur. Owner scope wajib dipaksakan repository dan composite FK untuk relasi private; ID valid saja bukan authorization.

## Identity dan Plans

| Table | Kolom/constraint minimum |
|---|---|
| `users` | id PK, normalized_email UNIQUE, email_verified_at, status, auth_epoch, locale, timezone |
| `password_credentials` | user_id PK/FK, password_hash, algorithm/params version, changed_at |
| `auth_identities` | id, user_id, provider, subject; UNIQUE(provider,subject) |
| `auth_sessions` | id, user_id, family_id, refresh_hash UNIQUE, parent_id, expires_at, used_at, revoked_at, auth_epoch |
| `auth_action_tokens` | id, user_id, purpose, token_hash UNIQUE, expires_at, consumed_at |
| `oauth_transactions` | id, state_hash UNIQUE, nonce_hash, encrypted_pkce_verifier, intent login/link, bound_user_id nullable, browser_binding_hash, redirect_ref, expires_at, consumed_at |
| `user_profiles` | user_id PK/FK, display_name, english_level, learning_goals/preferences JSONB |
| `plans` | code PK (`vip`, `advance`), policy_version, status; no subscription period |
| `user_plan_selections` | user_id PK/FK, plan_code FK nullable until selected, revision, selected_at |
| `plan_change_events` | id, user_id, old/new plan, revision, reason, created_at; immutable |

## Katalog, Secrets, Agents, Runtime

| Table | Kolom/constraint minimum |
|---|---|
| `provider_catalog` | id, code UNIQUE, status, canonical_base_url, allows_custom_base_url, base_url_policy JSONB; active seed Gemini/OpenAI |
| `ai_model_configurations` | id, provider_id, identifier, revision, capabilities, adapter/version, metering_contract, limits, status; UNIQUE(provider,identifier,revision) |
| `user_ai_credentials` | id, user_id, provider_id, encrypted_api_key/base_url, key_version, fingerprint, status, verified_at, revoked_at; multiple per provider allowed |
| `user_ai_selections` | user_id, capability (`llm`,`stt`), credential_id, model_id; PK(user,capability); credential owner/provider must match model |
| `agents` | id, code UNIQUE (`elean`,`willy`), display_name, status, active_version_id |
| `agent_versions` | id, agent_id, revision, persona_artifact_ref/hash, voice_binding_ref, knowledge_version, status; UNIQUE(agent,revision); published immutable |
| `ai_flow_registry` | id, environment, purpose, flow_version, langflow_flow_id, input/output schema version, prompt version, required capabilities, tool allowlist, timeout, status; one active per env/purpose |
| `tool_registry` | id, environment, name, schema_version, callcraft_spec_id, scope, idempotency_policy, timeout, status; UNIQUE(env,name,version) |
| `runtime_snapshots` | id, user_id, plan/revision, model/credential IDs, agent version refs, prompt/flow version, embedding profile, rate_card_version_id nullable, policy JSONB; immutable, no plaintext secrets |

Agent rename migration jika data legacy ditemukan: ubah nama/code ke Elean dengan mempertahankan ID/FK, resolve unique conflict eksplisit, versi persona baru dan reindex alias terkait; jangan membuat agent ketiga atau mengganti ID historis.

## Billing

| Table | Kolom/constraint minimum |
|---|---|
| `token_package_versions` | id, package_code, revision, currency, amount_minor > 0, token_units > 0, display_scale, status; published immutable |
| `rate_card_versions` | id, version UNIQUE, effective_at, status, rounding policy; immutable setelah dipakai |
| `rate_card_items` | id, rate_card_id, model_id, capability, meter, quantity_unit, cost_numerator >= 0, cost_denominator > 0; UNIQUE(card,model,capability,meter) |
| `wallets` | id, user_id UNIQUE, asset, available_units >= 0, held_units >= 0, version |
| `ledger_accounts` | id, wallet_id nullable, account_type, asset; unique logical account |
| `ledger_journals` | id, business_key UNIQUE, operation_ref, kind, reversal_of nullable, created_at; immutable |
| `ledger_entries` | id, journal_id, account_id, signed_units, asset; immutable; sum per journal/asset = 0 enforced deferred transaction constraint |
| `usage_reservations` | id, wallet_id, operation_id UNIQUE, rate_card_id, authorized_units, settled_units, released_units, state, lease/deadline; settled+released <= authorized |
| `ai_invocations` | id, operation_id, runtime_snapshot_id, capability, payer, provider_request_id, state, reservation_id nullable, started/ended_at; unique logical invocation key |
| `usage_records` | id, invocation_id, meter, meter_version, cumulative_quantity >= 0, sequence, provider_evidence_ref, final; UNIQUE(invocation,meter,sequence) |
| `usage_settlements` | id, invocation_id, revision, cumulative_charge, journal_id, rate_card_id; UNIQUE(invocation,revision) |
| `payment_orders` | id, user_id, package_version_id, merchant_reference UNIQUE, provider_payment_id UNIQUE nullable, amount_minor, currency, token_units, checkout_url, state, expires_at, paid_at |
| `webhook_inbox` | id, provider, provider_event_key, payload_hash, redacted/encrypted restricted payload_ref, state, attempts, received_at; UNIQUE(provider,event_key) |
| `payment_refunds` | id, order_id, provider_refund_id UNIQUE nullable, request_key UNIQUE, amount_minor, token_units, journal_id nullable, state |
| `payment_disputes` | id, order_id, provider_dispute_ref UNIQUE, outstanding_units, status, evidence_ref |

Wallet caches et ledger wajib satu transaksi; database trigger/privilege mencegah journal/entry UPDATE/DELETE. Reservation state `active|settling|settled|released|reconciliation_required`. Provider usage tidak diterima dari client/LLM. Webhook receipt terpisah dari status payment.

## Conversations, Calls, dan Media

| Table | Kolom/constraint minimum |
|---|---|
| `practice_categories` | id, code UNIQUE, title, status, sort_order |
| `conversation_sessions` | id, user_id, kind chat/call/podcast, state, runtime_snapshot_id, started_at, ended_at |
| `practice_sessions` | session_id PK/FK, category_id, agent_version_id |
| `conversation_messages` | id, session_id, owner_user_id, role, agent_version_id nullable, modality, text, media_id nullable, sequence, client_key nullable, generated/delivered spans, terminal_state, created_at; UNIQUE(session,sequence), UNIQUE(session,client_key) |
| `call_sessions` | session_id PK/FK, mode voice/video, room_name UNIQUE, room_sid nullable UNIQUE, state, end_reason, consent_version, lease_owner, fencing_token, lease_until |
| `call_turns` | id, session_id, sequence, speaker, message_id FK, state, epoch, timing/latencies, interrupted_at; UNIQUE(session,sequence) |
| `media_objects` | id, owner_user_id, storage_key UNIQUE, media_type, bytes, checksum, status, scan_state, retention_until |
| `conversation_extractions` | id, session_id, owner_user_id, source_start/end, schema_version, flow_version, data, created_at; UNIQUE(session,range,schema,flow_version) |
| `user_facts` | id, user_id, fact_key, value, confidence, status proposed/confirmed/superseded/rejected, provenance_ref, source_version, supersedes_id, consent_scope |
| `learning_assessments` | id, user_id, session_id, evidence_range, rubric_version, dimensions, suggested_level, flow_version; unique evidence/rubric/flow |

Shared `conversation_sessions` avoids polymorphic FK without integrity. Call/podcast messages use the same history contract. Append-only source data corrected via new revision/tombstone, not silent mutation.

## Podcast dan Knowledge

| Table | Kolom/constraint minimum |
|---|---|
| `podcasts` | id, user_id, title, state, current_source_version_id, current_script_version_id, generation_job_id nullable |
| `podcast_source_versions` | id, podcast_id, revision, media_id, checksum, parse_status, page_count; UNIQUE(podcast,revision) |
| `podcast_script_versions` | id, podcast_id, source_version_id, revision, runtime_snapshot_id, outline, target_duration, estimated_duration, status; immutable ready version |
| `podcast_segments` | id, script_version_id, position, agent_version_id, text, citations, estimated_ms; UNIQUE(script,position) |
| `podcast_playbacks` | id, podcast_id, script_version_id, session_id UNIQUE FK, room_name UNIQUE, state, segment_cursor, offset_ms, branch_ref, epoch, elapsed_ms, deadline_at, lease/fencing fields, end_reason |
| `podcast_audio_cache` | id, owner_user_id, segment_id, voice_config_hash, media_id, checksum; UNIQUE(segment,voice_config_hash) |
| `knowledge_documents` | id, scope, owner_user_id nullable, source_type/id/version, agent_id nullable, podcast_id nullable, status, canonical_object_ref, content_hash, deleted_at |
| `knowledge_chunks` | id, document_id, source_version, position, text/object_ref, content_hash, page/section refs; UNIQUE(document,version,position) |
| `embedding_profiles` | id, provider_id, model_id, model_revision, dimension > 0, task_type/normalization, generation, status |
| `vector_collection_registry` | id, environment, scope, profile_id, physical_name, status; UNIQUE(env,scope,profile) |
| `embedding_projections` | id, chunk_id, source_version, profile_id, generation, vector_id, state, content_hash, updated_at; UNIQUE(chunk,version,profile,generation) |

Private document owner mandatory; agent/public learning knowledge owner null only if published via admin authority. Canonical source/chunk data must support complete rebuild of both embeddings.

## Learn, Vocabulary, TOEFL

| Table | Kolom/constraint minimum |
|---|---|
| `courses`, `course_units`, `lessons` | hierarchy FK, slug/title/level/status, position unique within parent |
| `learning_content_versions` | id, lesson_id, revision, content_type, body/media refs, publication_state, published_at; UNIQUE(lesson,revision); published immutable |
| `learning_progress` | id, user_id, content_version_id, status, completion_percent CHECK 0..100, last_activity_at; UNIQUE(user,content_version) |
| `vocabulary_entries` | id, user_id, lemma, normalized_lemma, language, definition/example, provenance, state, next_review_at, mastery_score; UNIQUE(user,normalized_lemma,language) |
| `vocabulary_reviews` | id, entry_id, result, previous/new state, reviewed_at; immutable |
| `toefl_test_versions` | id, code, revision, rubric_version, definition, publication_state; UNIQUE(code,revision) |
| `toefl_attempts` | id, user_id, test_version_id, runtime_snapshot_id, state, submitted/evaluated_at |
| `toefl_submissions` | id, attempt_id, question_ref, section, answer/media refs; UNIQUE(attempt,question); locked after submit |
| `toefl_scores` | id, attempt_id, rubric_version, objective/subjective dimensions, bounded total, feedback, flow_version, review_status; UNIQUE(attempt,rubric_version) |

## Reliability

`idempotency_records`: principal/operation/key UNIQUE, request_hash, result/status, expires_at. Payment/ledger dedupe persists beyond generic request TTL. `outbox_events`: event_id PK, aggregate/version/type, reference payload, occurred/published_at. `background_jobs`: dedupe key UNIQUE, outbox ref, purpose, state, attempt_count, run_after, locked_until, fencing token, checkpoint, safe error, trace_id. `tool_executions`: user/tool/idempotency UNIQUE, request hash, authorized context refs, result ref/status. `audit_events`: append-only actor/action/target/result/correlation/redacted metadata. `deletion_requests`: user/source scope, tombstone version, per-store progress and legal retention status.

## Migrations dan States

Alembic forward-only shared environments, reviewed compensating rollback. State fields CHECK constrained; invalid transitions 409 with optimistic version. Jobs `pending → running → succeeded`, transient `retry_scheduled`, terminal `failed|cancelled`. TOEFL `created → in_progress → submitted → evaluating → evaluated|evaluation_failed`. Plan/catalog/voice seeding audited and explicit; no production mock rows or plaintext env values copied to SQL. Deletion financial records follows retention/anonymization policy while private learning/vector/media data is removed.
