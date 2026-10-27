"""创建知识领域并加入首批计算机与英语领域。"""

import sqlalchemy as sa
from alembic import op

revision = "0001_domains"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    domains = op.create_table(
        "domains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.UniqueConstraint("code", name="uq_domains_code"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.bulk_insert(
        domains,
        [
            {"id": 1, "code": "computer", "name": "计算机"},
            {"id": 2, "code": "english", "name": "英语"},
        ],
    )


def downgrade():
    op.drop_table("domains")
