"""Phase 3: conversations, vocabulary, knowledge, dan embedding schema

Revision ID: 0003_phase3_text_slice
Revises: 0002_phase2_catalog_billing
Create Date: 2025-09-24

Schema sesuai .blueprint/postgresql-schema.md untuk scope Phase 3 text
vertical slice: practice categories/sessions/messages, vocabulary, knowledge
canonical, embedding profiles/registry/projections. Call/podcast/media tables
mengikuti slice pemiliknya (Phase 5–7), bukan stub di sini.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_phase3_text_slice"
down_revision = "0002_phase2_catalog_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Practice categories ---
    op.create_table(
        "practice_categories",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','retired')", name="ck_practice_categories_status"
        ),
    )

    # --- Conversation sessions (shared chat/call/podcast) ---
    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "runtime_snapshot_id",
            sa.String(26),
            sa.ForeignKey("runtime_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind IN ('chat','call','podcast')", name="ck_conversation_sessions_kind"
        ),
        sa.CheckConstraint(
            "state IN ('active','completed','abandoned')", name="ck_conversation_sessions_state"
        ),
    )
    op.create_index("ix_conversation_sessions_user_id", "conversation_sessions", ["user_id"])
    op.create_index("ix_conversation_sessions_state", "conversation_sessions", ["state"])

    op.create_table(
        "practice_sessions",
        sa.Column(
            "session_id",
            sa.String(26),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "category_id",
            sa.String(26),
            sa.ForeignKey("practice_categories.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "agent_version_id",
            sa.String(26),
            sa.ForeignKey("agent_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_index("ix_practice_sessions_category_id", "practice_sessions", ["category_id"])

    # --- Conversation messages (append-only source data) ---
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(26),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.String(26),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column(
            "agent_version_id",
            sa.String(26),
            sa.ForeignKey("agent_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("modality", sa.String(20), nullable=False, server_default="text"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("media_id", sa.String(26), nullable=True),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("client_key", sa.String(128), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_state", sa.String(20), nullable=False, server_default="completed"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("session_id", "sequence", name="uq_conversation_messages_sequence"),
        sa.UniqueConstraint("session_id", "client_key", name="uq_conversation_messages_client_key"),
        sa.CheckConstraint(
            "role IN ('user','agent','system')", name="ck_conversation_messages_role"
        ),
        sa.CheckConstraint(
            "modality IN ('text','audio','image')", name="ck_conversation_messages_modality"
        ),
        sa.CheckConstraint(
            "terminal_state IN ('completed','cancelled','failed')",
            name="ck_conversation_messages_terminal_state",
        ),
    )
    op.create_index("ix_conversation_messages_session_id", "conversation_messages", ["session_id"])
    op.create_index(
        "ix_conversation_messages_owner_user_id", "conversation_messages", ["owner_user_id"]
    )

    # --- Conversation extractions (canonical ingestion output) ---
    op.create_table(
        "conversation_extractions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(26),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.String(26),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_start", sa.Integer, nullable=False),
        sa.Column("source_end", sa.Integer, nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("flow_version", sa.String(40), nullable=False),
        sa.Column("data", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "session_id",
            "source_start",
            "source_end",
            "schema_version",
            "flow_version",
            name="uq_conversation_extractions_range",
        ),
        sa.CheckConstraint("source_end >= source_start", name="ck_conversation_extractions_range"),
    )
    op.create_index(
        "ix_conversation_extractions_owner_user_id", "conversation_extractions", ["owner_user_id"]
    )

    # --- User facts (provenance, consent) ---
    op.create_table(
        "user_facts",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("fact_key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("provenance_ref", sa.Text, nullable=True),
        sa.Column("source_version", sa.String(40), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.String(26),
            sa.ForeignKey("user_facts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("consent_scope", sa.String(40), nullable=False, server_default="learning"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_user_facts_confidence"),
        sa.CheckConstraint(
            "status IN ('proposed','confirmed','superseded','rejected')",
            name="ck_user_facts_status",
        ),
    )
    op.create_index("ix_user_facts_user_id", "user_facts", ["user_id"])
    op.create_index("ix_user_facts_fact_key", "user_facts", ["fact_key"])

    # --- Learning assessments ---
    op.create_table(
        "learning_assessments",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "session_id",
            sa.String(26),
            sa.ForeignKey("conversation_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("evidence_start", sa.Integer, nullable=False),
        sa.Column("evidence_end", sa.Integer, nullable=False),
        sa.Column("rubric_version", sa.String(40), nullable=False),
        sa.Column("dimensions", sa.Text, nullable=False),
        sa.Column("suggested_level", sa.String(20), nullable=True),
        sa.Column("flow_version", sa.String(40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "session_id",
            "evidence_start",
            "evidence_end",
            "rubric_version",
            "flow_version",
            name="uq_learning_assessments_evidence",
        ),
    )
    op.create_index("ix_learning_assessments_user_id", "learning_assessments", ["user_id"])

    # --- Vocabulary ---
    op.create_table(
        "vocabulary_entries",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("lemma", sa.String(255), nullable=False),
        sa.Column("normalized_lemma", sa.String(255), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("definition", sa.Text, nullable=True),
        sa.Column("example", sa.Text, nullable=True),
        sa.Column("provenance", sa.Text, nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="new"),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mastery_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "normalized_lemma", "language", name="uq_vocabulary_lemma"),
        sa.CheckConstraint(
            "state IN ('new','learning','review','mastered','archived')",
            name="ck_vocabulary_entries_state",
        ),
        sa.CheckConstraint(
            "mastery_score >= 0 AND mastery_score <= 100", name="ck_vocabulary_mastery"
        ),
    )
    op.create_index("ix_vocabulary_entries_user_id", "vocabulary_entries", ["user_id"])

    op.create_table(
        "vocabulary_reviews",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "entry_id",
            sa.String(26),
            sa.ForeignKey("vocabulary_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("previous_state", sa.String(20), nullable=False),
        sa.Column("new_state", sa.String(20), nullable=False),
        sa.Column(
            "reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "result IN ('again','hard','good','easy')", name="ck_vocabulary_reviews_result"
        ),
    )
    op.create_index("ix_vocabulary_reviews_entry_id", "vocabulary_reviews", ["entry_id"])

    # --- Knowledge canonical documents & chunks ---
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.String(26),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(26), nullable=False),
        sa.Column("source_version", sa.String(40), nullable=False),
        sa.Column(
            "agent_id",
            sa.String(26),
            sa.ForeignKey("agents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("podcast_id", sa.String(26), nullable=True),
        sa.Column("publication_state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("indexing_state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("canonical_object_ref", sa.Text, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "scope IN ('user_memory','agent_knowledge','learning_content',"
            "'toefl_feedback','podcast_sources')",
            name="ck_knowledge_documents_scope",
        ),
        sa.CheckConstraint(
            "publication_state IN ('draft','published','archived')",
            name="ck_knowledge_documents_publication",
        ),
        sa.CheckConstraint(
            "indexing_state IN ('pending','partial','ready','failed','deleted')",
            name="ck_knowledge_documents_indexing",
        ),
        # Private document wajib owner; shared hanya bila scope bukan user_memory
        sa.CheckConstraint(
            "(scope = 'user_memory' AND owner_user_id IS NOT NULL) OR (scope <> 'user_memory')",
            name="ck_knowledge_documents_owner_scope",
        ),
    )
    op.create_index(
        "ix_knowledge_documents_owner_user_id", "knowledge_documents", ["owner_user_id"]
    )
    op.create_index("ix_knowledge_documents_scope", "knowledge_documents", ["scope"])
    op.create_index(
        "ix_knowledge_documents_source",
        "knowledge_documents",
        ["source_type", "source_id", "source_version"],
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(26),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_version", sa.String(40), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=True),
        sa.Column("object_ref", sa.Text, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("page_ref", sa.Integer, nullable=True),
        sa.Column("section_ref", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "document_id", "source_version", "position", name="uq_knowledge_chunks_position"
        ),
        # Chunk wajib punya text inline ATAU object ref, tidak keduanya kosong
        sa.CheckConstraint(
            "text IS NOT NULL OR object_ref IS NOT NULL",
            name="ck_knowledge_chunks_content",
        ),
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])

    # --- Embedding profiles & vector collection registry ---
    op.create_table(
        "embedding_profiles",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "provider_id",
            sa.String(26),
            sa.ForeignKey("provider_catalog.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "model_id",
            sa.String(26),
            sa.ForeignKey("ai_model_configurations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model_revision", sa.Integer, nullable=False),
        sa.Column("dimension", sa.Integer, nullable=False),
        sa.Column("document_task_type", sa.String(40), nullable=False),
        sa.Column("query_task_type", sa.String(40), nullable=False),
        sa.Column("normalization", sa.String(40), nullable=False),
        sa.Column("generation", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="staged"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "provider_id",
            "model_id",
            "model_revision",
            "generation",
            name="uq_embedding_profiles_generation",
        ),
        sa.CheckConstraint("dimension > 0", name="ck_embedding_profiles_dimension"),
        sa.CheckConstraint(
            "status IN ('staged','active','disabled')", name="ck_embedding_profiles_status"
        ),
    )

    op.create_table(
        "vector_collection_registry",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("environment", sa.String(40), nullable=False),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column(
            "profile_id",
            sa.String(26),
            sa.ForeignKey("embedding_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("physical_name", sa.String(128), nullable=False),
        sa.Column("similarity_metric", sa.String(20), nullable=False),
        sa.Column("metadata_index_policy", sa.Text, nullable=True),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="staged"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "environment", "scope", "profile_id", name="uq_vector_registry_binding"
        ),
        sa.UniqueConstraint("environment", "physical_name", name="uq_vector_registry_physical"),
        sa.CheckConstraint(
            "similarity_metric IN ('cosine','dot_product','euclidean')",
            name="ck_vector_registry_metric",
        ),
        sa.CheckConstraint(
            "status IN ('staged','active','disabled')", name="ck_vector_registry_status"
        ),
    )

    op.create_table(
        "embedding_projections",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "chunk_id",
            sa.String(26),
            sa.ForeignKey("knowledge_chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_version", sa.String(40), nullable=False),
        sa.Column(
            "profile_id",
            sa.String(26),
            sa.ForeignKey("embedding_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column("vector_id", sa.String(128), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "chunk_id",
            "source_version",
            "profile_id",
            "generation",
            name="uq_embedding_projections_target",
        ),
        sa.CheckConstraint(
            "state IN ('pending','projected','failed','deleted')",
            name="ck_embedding_projections_state",
        ),
    )
    op.create_index("ix_embedding_projections_chunk_id", "embedding_projections", ["chunk_id"])
    op.create_index("ix_embedding_projections_state", "embedding_projections", ["state"])

    # --- Append-only protections ---
    for table in ("conversation_messages", "conversation_extractions", "vocabulary_reviews"):
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_mutation();"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_mutation();"
        )


def downgrade() -> None:
    for table in ("conversation_messages", "conversation_extractions", "vocabulary_reviews"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete ON {table};")

    op.drop_table("embedding_projections")
    op.drop_table("vector_collection_registry")
    op.drop_table("embedding_profiles")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    op.drop_table("vocabulary_reviews")
    op.drop_table("vocabulary_entries")
    op.drop_table("learning_assessments")
    op.drop_table("user_facts")
    op.drop_table("conversation_extractions")
    op.drop_table("conversation_messages")
    op.drop_table("practice_sessions")
    op.drop_table("conversation_sessions")
    op.drop_table("practice_categories")
