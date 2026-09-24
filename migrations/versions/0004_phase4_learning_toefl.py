"""Phase 4: learning content, progress, dan TOEFL schema

Revision ID: 0004_phase4_learning_toefl
Revises: 0003_phase3_text_slice
Create Date: 2025-09-24

Schema sesuai .blueprint/postgresql-schema.md bagian Learn/Vocabulary/TOEFL.
Vocabulary sudah dibuat pada 0003.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_phase4_learning_toefl"
down_revision = "0003_phase3_text_slice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Learning hierarchy ---
    op.create_table(
        "courses",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status IN ('draft','published','retired')", name="ck_courses_status"),
    )

    op.create_table(
        "course_units",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "course_id",
            sa.String(26),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("course_id", "position", name="uq_course_units_position"),
    )
    op.create_index("ix_course_units_course_id", "course_units", ["course_id"])

    op.create_table(
        "lessons",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "unit_id",
            sa.String(26),
            sa.ForeignKey("course_units.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("unit_id", "position", name="uq_lessons_position"),
        sa.UniqueConstraint("unit_id", "slug", name="uq_lessons_slug"),
        sa.CheckConstraint("status IN ('draft','published','retired')", name="ck_lessons_status"),
    )
    op.create_index("ix_lessons_unit_id", "lessons", ["unit_id"])

    op.create_table(
        "learning_content_versions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "lesson_id",
            sa.String(26),
            sa.ForeignKey("lessons.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("content_type", sa.String(40), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("media_refs", sa.Text, nullable=True),
        sa.Column("publication_state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("lesson_id", "revision", name="uq_learning_content_revision"),
        sa.CheckConstraint(
            "publication_state IN ('draft','published','archived')",
            name="ck_learning_content_publication",
        ),
    )
    op.create_index(
        "ix_learning_content_versions_lesson_id", "learning_content_versions", ["lesson_id"]
    )

    op.create_table(
        "learning_progress",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "content_version_id",
            sa.String(26),
            sa.ForeignKey("learning_content_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="started"),
        sa.Column("completion_percent", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "user_id", "content_version_id", name="uq_learning_progress_user_content"
        ),
        sa.CheckConstraint(
            "completion_percent >= 0 AND completion_percent <= 100",
            name="ck_learning_progress_percent",
        ),
        sa.CheckConstraint("status IN ('started','completed')", name="ck_learning_progress_status"),
    )
    op.create_index("ix_learning_progress_user_id", "learning_progress", ["user_id"])

    # --- TOEFL ---
    op.create_table(
        "toefl_test_versions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("rubric_version", sa.String(40), nullable=False),
        sa.Column("definition", sa.Text, nullable=False),
        sa.Column("publication_state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("code", "revision", name="uq_toefl_test_revision"),
        sa.CheckConstraint(
            "publication_state IN ('draft','published','archived')",
            name="ck_toefl_test_publication",
        ),
    )

    op.create_table(
        "toefl_attempts",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "test_version_id",
            sa.String(26),
            sa.ForeignKey("toefl_test_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "runtime_snapshot_id",
            sa.String(26),
            sa.ForeignKey("runtime_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.String(20), nullable=False, server_default="created"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "state IN ('created','in_progress','submitted','evaluating',"
            "'evaluated','evaluation_failed')",
            name="ck_toefl_attempts_state",
        ),
    )
    op.create_index("ix_toefl_attempts_user_id", "toefl_attempts", ["user_id"])

    op.create_table(
        "toefl_submissions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.String(26),
            sa.ForeignKey("toefl_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_ref", sa.String(80), nullable=False),
        sa.Column("section", sa.String(20), nullable=False),
        sa.Column("answer", sa.Text, nullable=True),
        sa.Column("media_refs", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("attempt_id", "question_ref", name="uq_toefl_submissions_question"),
        sa.CheckConstraint(
            "section IN ('reading','listening','speaking','writing')",
            name="ck_toefl_submissions_section",
        ),
    )
    op.create_index("ix_toefl_submissions_attempt_id", "toefl_submissions", ["attempt_id"])

    op.create_table(
        "toefl_scores",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.String(26),
            sa.ForeignKey("toefl_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("rubric_version", sa.String(40), nullable=False),
        sa.Column("objective_dimensions", sa.Text, nullable=False),
        sa.Column("subjective_dimensions", sa.Text, nullable=True),
        sa.Column("total_score", sa.Integer, nullable=False),
        sa.Column("feedback", sa.Text, nullable=True),
        sa.Column("flow_version", sa.String(40), nullable=True),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="final"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("attempt_id", "rubric_version", name="uq_toefl_scores_rubric"),
        sa.CheckConstraint("total_score >= 0 AND total_score <= 120", name="ck_toefl_scores_total"),
        sa.CheckConstraint(
            "review_status IN ('final','pending_review','reviewed')", name="ck_toefl_scores_review"
        ),
    )
    op.create_index("ix_toefl_scores_attempt_id", "toefl_scores", ["attempt_id"])

    # Append-only protections
    for table in ("toefl_scores",):
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_mutation();"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_mutation();"
        )


def downgrade() -> None:
    for table in ("toefl_scores",):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete ON {table};")
    op.drop_table("toefl_scores")
    op.drop_table("toefl_submissions")
    op.drop_table("toefl_attempts")
    op.drop_table("toefl_test_versions")
    op.drop_table("learning_progress")
    op.drop_table("learning_content_versions")
    op.drop_table("lessons")
    op.drop_table("course_units")
    op.drop_table("courses")
