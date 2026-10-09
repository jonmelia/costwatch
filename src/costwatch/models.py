from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime


@dataclass
class ScanConfig:
    snapshot_age_days: int = 90
    stopped_days: int = 30
    # Idle checks look at this many days of CloudWatch metrics
    idle_days: int = 14
    idle_cpu_percent: float = 5.0
    log_group_min_gib: float = 1.0
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
    tags: dict[str, str] = field(default_factory=dict)
    owner: str | None = None
    owner_source: str | None = None  # e.g. "tag:Owner", "cloudtrail"
    managed_by: str | None = None  # "terraform", "cloudformation" or "unmanaged"
    iac_address: str | None = None  # Terraform address or CloudFormation stack name
    iac_source: str | None = None  # state file the address came from

    def to_dict(self) -> dict:
        return asdict(self)
