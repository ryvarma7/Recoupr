"""final pass: simulation scoping and late recovery marker"""
from alembic import op
import sqlalchemy as sa

revision = "c2f1e4a9b7d0"
down_revision = "d81c4a7e5f30"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.drop_constraint("uq_auditlog_id", "auditlogentry", type_="unique")
    op.add_column("case", sa.Column("simulation_run_id", sa.Integer(), nullable=True))
    op.create_index("ix_case_simulation_run_id", "case", ["simulation_run_id"], unique=False)
    op.create_foreign_key("fk_case_simulation_run_id", "case", "simulationrun", ["simulation_run_id"], ["id"])
    op.add_column("outcome", sa.Column("late_recovery_after_ttl", sa.Boolean(), nullable=False, server_default=sa.false()))

def downgrade() -> None:
    op.drop_column("outcome", "late_recovery_after_ttl")
    op.drop_constraint("fk_case_simulation_run_id", "case", type_="foreignkey")
    op.drop_index("ix_case_simulation_run_id", table_name="case")
    op.drop_column("case", "simulation_run_id")
