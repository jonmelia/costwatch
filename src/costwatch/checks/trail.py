from datetime import timedelta

import boto3

from costwatch.aws import client
from costwatch.models import Finding, ScanConfig

_PAID_EVENTS = "PaidEventsRecorded"
_MIN_MONTHLY = 1.0  # below this a second copy isn't worth anyone's time
_RECOMMENDATION = (
    "AWS gives you one copy of management events free and charges for every other. If this "
    "trail is there for data events, turn off its management events. If nothing needs it, "
    "delete it. Check nothing relies on its logs first"
)


def _paid_events_monthly(session: boto3.Session, account: str, config: ScanConfig) -> float:
    """What the last 30 days of the bill charged for extra copies of management events. Cost
    Explorer charges $0.01 a request, so this only runs when an account has a second trail."""
    ce = client(session, "ce", "us-east-1")
    kwargs = {
        "TimePeriod": {
            "Start": (config.now - timedelta(days=30)).date().isoformat(),
            "End": config.now.date().isoformat(),
        },
        "Granularity": "MONTHLY",
        "Metrics": ["UnblendedCost"],
        "GroupBy": [{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
        "Filter": {"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [account]}},
    }
    total = 0.0
    while True:
        page = ce.get_cost_and_usage(**kwargs)
        for period in page["ResultsByTime"]:
            for group in period["Groups"]:
                if _PAID_EVENTS in group["Keys"][0]:
                    total += float(group["Metrics"]["UnblendedCost"]["Amount"])
        if not page.get("NextPageToken"):
            return total
        kwargs["NextPageToken"] = page["NextPageToken"]


def duplicate_trails(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    """Trails beyond the first, priced from the bill. Reported in the trail's home region, so a
    multi-region trail isn't found once per region. An organization trail is never the extra one:
    it can't be turned off from a member account, and it's usually the record to keep."""
    trails = client(session, "cloudtrail", region).describe_trails(includeShadowTrails=True)[
        "trailList"
    ]
    extras = [
        t for t in trails if not t.get("IsOrganizationTrail") and t.get("HomeRegion") == region
    ]
    if len(trails) < 2 or not extras:
        return []
    account = client(session, "sts", region).get_caller_identity()["Account"]
    monthly = _paid_events_monthly(session, account, config)
    if monthly < _MIN_MONTHLY:
        return []
    return [
        Finding(
            check="duplicate-trail",
            region=region,
            resource_id=trail["TrailARN"],
            name=trail["Name"],
            tags={},
            description=(
                f"One of {len(trails)} trails in this account; the bill shows "
                f"${monthly:.2f} in the last 30 days for extra copies of management events"
            ),
            monthly_cost=monthly / len(extras),
            recommendation=_RECOMMENDATION,
        )
        for trail in extras
    ]
