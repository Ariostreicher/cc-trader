"""initial schema

Revision ID: 20260512_0001
Revises:
Create Date: 2026-05-12

Single initial migration generated from app/models/*. In a real workflow you
would run `alembic revision --autogenerate -m "initial"` against an empty DB
and let Alembic produce this; the file is committed verbatim here so the
stack boots without an extra step.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260512_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: SQLAlchemy create_all path is used here for brevity.
    # Run from the backend container:  alembic upgrade head
    from app.db.base import Base
    from app import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    from app.db.base import Base
    from app import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
