"""Work out who owns each finding: owner tags first, then who created it per CloudTrail.

CloudTrail's LookupEvents only covers the last 90 days of management events and is limited to
2 requests per second per region, so lookups run one at a time per region with adaptive retries.
"""

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from costwatch.aws import client, short_error
from costwatch.models import Finding

OWNER_TAG_KEYS = ("owner", "createdby", "created-by", "created_by", "contact", "team")

CREATE_EVENTS = {
    "unattached-ebs-volume": {"CreateVolume"},
    "old-ebs-snapshot": {"CreateSnapshot", "CreateSnapshots", "CopySnapshot"},
    "unused-elastic-ip": {"AllocateAddress"},
    "long-stopped-instance": {"RunInstances"},
    "idle-load-balancer": {"CreateLoadBalancer"},
    "old-rds-snapshot": {
        "CreateDBSnapshot",
        "CreateDBClusterSnapshot",
        "CopyDBSnapshot",
        "CopyDBClusterSnapshot",
    },
}

_RETRY_CONFIG = Config(retries={"mode": "adaptive", "max_attempts": 10})
_MAX_PAGES = 3


def owner_from_tags(tags: dict[str, str]) -> tuple[str, str] | None:
    """Return (owner, tag key) for the first owner-like tag."""
    lowered = {k.lower(): (k, v) for k, v in tags.items() if v}
    for key in OWNER_TAG_KEYS:
        if key in lowered:
            original_key, value = lowered[key]
            return value, original_key
    return None


def identity(event: dict) -> str | None:
    """Human-friendly 'who' for a LookupEvents event."""
    detail = json.loads(event.get("CloudTrailEvent") or "{}")
    user = detail.get("userIdentity", {})
    match user.get("type"):
        case "AssumedRole":
            # arn:aws:sts::123:assumed-role/RoleName/session -> "session (RoleName)"
            parts = user.get("arn", "").split("/")
            if len(parts) >= 3:
                return f"{parts[2]} ({parts[1]})"
        case "IAMUser":
            return user.get("userName") or event.get("Username")
        case "Root":
            return "root"
        case "AWSService":
            return user.get("invokedBy")
    return event.get("Username")


def _creator(trail, finding: Finding) -> str | None:
    wanted = CREATE_EVENTS.get(finding.check, set())
    for value in dict.fromkeys(v for v in (finding.resource_id, finding.name) if v):
        pages = trail.get_paginator("lookup_events").paginate(
            LookupAttributes=[{"AttributeKey": "ResourceName", "AttributeValue": value}],
            PaginationConfig={"MaxItems": 50 * _MAX_PAGES, "PageSize": 50},
        )
        for page in pages:
            for event in page["Events"]:
                if event.get("EventName") in wanted:
                    return identity(event)
    return None


def _resolve_region(session: boto3.Session, region: str, findings: list[Finding]) -> list[str]:
    trail = client(session, "cloudtrail", region, config=_RETRY_CONFIG)
    for finding in findings:
        try:
            who = _creator(trail, finding)
        except (ClientError, BotoCoreError) as e:
            return [f"{region} owner lookup: {short_error(e)}"]
        if who:
            finding.owner, finding.owner_source = who, "cloudtrail"
    return []


def resolve_owners(
    session: boto3.Session, findings: list[Finding], max_workers: int = 8
) -> list[str]:
    """Fill in owner/owner_source on findings in place. Returns errors."""
    pending: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        if tagged := owner_from_tags(finding.tags):
            finding.owner, key = tagged
            finding.owner_source = f"tag:{key}"
        else:
            pending[finding.region].append(finding)

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for region_errors in pool.map(
            lambda item: _resolve_region(session, *item), pending.items()
        ):
            errors.extend(region_errors)
    return sorted(errors)
