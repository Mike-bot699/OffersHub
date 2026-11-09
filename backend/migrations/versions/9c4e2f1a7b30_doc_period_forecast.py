"""doc period forecast

Revision ID: 9c4e2f1a7b30
Revises: 65836c310d97
Create Date: 2026-11-09 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9c4e2f1a7b30'
down_revision: Union[str, Sequence[str], None] = '65836c310d97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('offer_docs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('forecast', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    with op.batch_alter_table('offer_docs', schema=None) as batch_op:
        batch_op.drop_column('forecast')
