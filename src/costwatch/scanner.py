from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from costwatch.aws import client, short_error
from costwatch.checks import ALL_CHECKS
from costwatch.models import Finding, ScanConfig

Check = Callable[[boto3.Session, str, ScanConfig], list[Finding]]


@dataclass
class ScanResult:
    account_id: str
    regions: list[str]
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    owners_checked: bool = False

    @property
    def total_monthly_cost(self) -> float:
        return sum(f.monthly_cost for f in self.findings)


def enabled_regions(session: boto3.Session) -> list[str]:
    ec2 = client(session, "ec2", session.region_name or "us-east-1")
    return sorted(r["RegionName"] for r in ec2.describe_regions()["Regions"])


def scan(
    session: boto3.Session,
    regions: list[str] | None = None,
    config: ScanConfig | None = None,
    checks: list[Check] | None = None,
    max_workers: int = 8,
) -> ScanResult:
    config = config or ScanConfig()
    # Fails fast on missing or expired credentials, before fanning out
    account_id = client(session, "sts", session.region_name or "us-east-1").get_caller_identity()[
        "Account"
    ]
    regions = regions or enabled_regions(session)
    result = ScanResult(account_id=account_id, regions=regions)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(check, session, region, config): (check.__name__, region)
            for region in regions
            for check in checks or ALL_CHECKS
        }
        for future in as_completed(futures):
            name, region = futures[future]
            try:
                result.findings.extend(future.result())
            except (ClientError, BotoCoreError) as e:
                # One denied API or disabled region shouldn't sink the whole scan
                result.errors.append(f"{region} {name}: {short_error(e)}")

    result.findings.sort(key=lambda f: f.monthly_cost, reverse=True)
    result.errors.sort()
    return result
