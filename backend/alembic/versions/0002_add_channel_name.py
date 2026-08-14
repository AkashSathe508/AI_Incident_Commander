"""add_channel_name_to_meetings

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-14

Adds the `channel_name` column to the meetings table.
This column stores the unique Agora RTC channel name for each meeting,
derived at creation time as "room-{meeting.id}".
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("channel_name", sa.String(255), nullable=True),
    )
    op.create_unique_constraint("uq_meetings_channel_name", "meetings", ["channel_name"])
    op.create_index("ix_meetings_channel_name", "meetings", ["channel_name"])


def downgrade() -> None:
    op.drop_index("ix_meetings_channel_name", table_name="meetings")
    op.drop_constraint("uq_meetings_channel_name", "meetings", type_="unique")
    op.drop_column("meetings", "channel_name")
