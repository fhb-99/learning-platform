"""管理员权限、独立草稿与发布来源"""

import sqlalchemy as sa
from alembic import op

revision = "0004_question_publishing"
down_revision = "0003_personal_review"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), server_default="0", nullable=False))
    op.add_column("question_versions", sa.Column("published_by", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_version_publisher", "question_versions", "users", ["published_by"], ["id"]
    )
    op.create_table(
        "question_drafts",
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("questions.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("correct_answer", sa.String(1), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("published_version_id", sa.Integer(), sa.ForeignKey("question_versions.id")),
    )


def downgrade():
    # 草稿与发布来源也是业务数据，不允许降级时悄悄删除。
    connection = op.get_bind()
    for query in (
        "SELECT COUNT(*) FROM question_drafts",
        "SELECT COUNT(*) FROM question_versions WHERE published_by IS NOT NULL",
        "SELECT COUNT(*) FROM users WHERE is_admin = 1",
    ):
        if connection.execute(sa.text(query)).scalar():
            raise RuntimeError("已有题库管理数据或管理员权限，拒绝降级，请先制定保留数据方案")
    op.drop_table("question_drafts")
    op.drop_constraint("fk_version_publisher", "question_versions", type_="foreignkey")
    op.drop_column("question_versions", "published_by")
    op.drop_column("users", "is_admin")
