import boto3

from costwatch import pricing
from costwatch.aws import client
from costwatch.models import Finding, ScanConfig


def log_groups_without_retention(
    session: boto3.Session, region: str, config: ScanConfig
) -> list[Finding]:
    logs = client(session, "logs", region)
    min_bytes = config.log_group_min_gib * 2**30
    findings = []
    for page in logs.get_paginator("describe_log_groups").paginate():
        for group in page["logGroups"]:
            stored = group.get("storedBytes", 0)
            if "retentionInDays" in group or stored < min_bytes:
                continue
            findings.append(
                Finding(
                    check="log-group-no-retention",
                    region=region,
                    resource_id=group["logGroupName"],
                    description=(
                        f"{stored / 2**30:,.1f} GiB of logs kept forever (no retention set)"
                    ),
                    monthly_cost=pricing.log_storage_monthly(stored),
                    recommendation="Set a retention period, e.g. 30–90 days, or export to S3.",
                )
            )
    return findings
