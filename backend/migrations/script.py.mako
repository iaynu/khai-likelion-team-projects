${message}

Revision ID: ${up_revision}
Revises: ${down_revision | n,'None'}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers
revision = '${up_revision}'
down_revision = ${down_revision | n,'None'}
branch_labels = ${branch_labels|n}
depends_on = ${depends_on | n,'None'}


def upgrade() -> None:
    ${upgrade()}


def downgrade() -> None:
    ${downgrade()}
"""
