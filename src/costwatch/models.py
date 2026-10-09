from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime


@dataclass
class ScanConfig:
    snapshot_age_days: int = 90
    stopped_days: int = 30
    # Overridable so tests can move the clock forward
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Finding:
    check: str
    region: str
    resource_id: str
    description: str
    monthly_cost: float  # estimated USD per month
    recommendation: str
    name: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)
