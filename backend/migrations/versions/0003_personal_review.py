"""个人复习作答来源与历史查询索引"""

import sqlalchemy as sa
from alembic import op

revision = "0003_personal_review"
down_revision = "0002_daily_practice"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("attempts", "daily_assignment_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("attempts", sa.Column("review_question_id", sa.Integer(), nullable=True))
    op.add_column("attempts", sa.Column("review_basis_attempt_id", sa.Integer(), nullable=True))
    op.add_column("attempts", sa.Column("scheduled_due_date", sa.Date(), nullable=True))
    op.create_foreign_key(
        "fk_attempt_review_question", "attempts", "questions", ["review_question_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_attempt_review_basis", "attempts", "attempts", ["review_basis_attempt_id"], ["id"]
    )
    op.create_unique_constraint(
        "uq_review_user_day", "attempts", ["user_id", "review_question_id", "local_date"]
    )
    op.create_check_constraint(
        "ck_attempt_source",
        "attempts",
        "(daily_assignment_id IS NOT NULL AND review_question_id IS NULL "
        "AND review_basis_attempt_id IS NULL AND scheduled_due_date IS NULL) OR "
        "(daily_assignment_id IS NULL AND review_question_id IS NOT NULL "
        "AND review_basis_attempt_id IS NOT NULL AND scheduled_due_date IS NOT NULL)",
    )
    op.create_index("ix_attempt_user_history", "attempts", ["user_id", "id"])
    op.create_index("ix_review_user_due", "review_plans", ["user_id", "due_date", "question_id"])


def downgrade():
    # 旧结构无法容纳复习事实，拒绝静默删除用户数据；必须先做数据归档决策。
    if (
        op.get_bind()
        .execute(sa.text("SELECT COUNT(*) FROM attempts WHERE daily_assignment_id IS NULL"))
        .scalar()
    ):
        raise RuntimeError("已有复习记录，拒绝降级丢失数据，请先制定保留复习数据的回退方案")
    op.drop_index("ix_review_user_due", table_name="review_plans")
    op.drop_index("ix_attempt_user_history", table_name="attempts")
    op.drop_constraint("ck_attempt_source", "attempts", type_="check")
    op.drop_constraint("fk_attempt_review_basis", "attempts", type_="foreignkey")
    op.drop_constraint("fk_attempt_review_question", "attempts", type_="foreignkey")
    op.drop_constraint("uq_review_user_day", "attempts", type_="unique")
    op.drop_column("attempts", "scheduled_due_date")
    op.drop_column("attempts", "review_basis_attempt_id")
    op.drop_column("attempts", "review_question_id")
    op.alter_column("attempts", "daily_assignment_id", existing_type=sa.Integer(), nullable=False)
