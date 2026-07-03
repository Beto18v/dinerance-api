"""add obligation cadence_months column

Revision ID: 3388ed43a6fd
Revises: e0f863a7acb3
Create Date: 2026-07-03 10:18:56.838730

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3388ed43a6fd'
down_revision: Union[str, Sequence[str], None] = 'e0f863a7acb3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "obligations",
        sa.Column("cadence_months", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_obligations_cadence_months_positive",
        "obligations",
        sa.sql.column("cadence_months").is_(None)
        | (sa.sql.column("cadence_months") >= 1),
    )


def downgrade() -> None:
    op.drop_constraint("ck_obligations_cadence_months_positive", "obligations")
    op.drop_column("obligations", "cadence_months")
