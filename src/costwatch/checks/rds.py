from datetime import timedelta

import boto3

from costwatch import pricing
from costwatch.aws import client
from costwatch.models import Finding, ScanConfig

# Automated backups expire with the retention period; manual snapshots are kept until deleted.
_RECOMMENDATION = (
    "Delete if no longer needed, or export to S3 (Glacier) for cheaper long-term retention."
)


def old_rds_snapshots(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    rds = client(session, "rds", region)
    cutoff = config.now - timedelta(days=config.snapshot_age_days)
    findings = []
    for page in rds.get_paginator("describe_db_snapshots").paginate(SnapshotType="manual"):
        for snap in page["DBSnapshots"]:
            created = snap.get("SnapshotCreateTime")
            if snap.get("Status") != "available" or not created or created > cutoff:
                continue
            size = snap.get("AllocatedStorage", 0)
            findings.append(
                Finding(
                    check="old-rds-snapshot",
                    region=region,
                    resource_id=snap["DBSnapshotArn"],
                    name=snap["DBSnapshotIdentifier"],
                    description=(
                        f"{size} GiB manual snapshot of {snap['Engine']} instance "
                        f"{snap['DBInstanceIdentifier']}, {(config.now - created).days} days old "
                        "(cost is an upper bound)"
                    ),
                    monthly_cost=pricing.rds_snapshot_monthly(size),
                    recommendation=_RECOMMENDATION,
                )
            )
    return findings


def old_rds_cluster_snapshots(
    session: boto3.Session, region: str, config: ScanConfig
) -> list[Finding]:
    rds = client(session, "rds", region)
    cutoff = config.now - timedelta(days=config.snapshot_age_days)
    findings = []
    pages = rds.get_paginator("describe_db_cluster_snapshots").paginate(SnapshotType="manual")
    for page in pages:
        for snap in page["DBClusterSnapshots"]:
            created = snap.get("SnapshotCreateTime")
            if snap.get("Status") != "available" or not created or created > cutoff:
                continue
            size = snap.get("AllocatedStorage", 0)
            aurora = snap.get("Engine", "").startswith("aurora")
            findings.append(
                Finding(
                    check="old-rds-snapshot",
                    region=region,
                    resource_id=snap["DBClusterSnapshotArn"],
                    name=snap["DBClusterSnapshotIdentifier"],
                    description=(
                        f"{size} GiB manual snapshot of {snap['Engine']} cluster "
                        f"{snap['DBClusterIdentifier']}, {(config.now - created).days} days old "
                        "(cost is an upper bound)"
                    ),
                    monthly_cost=pricing.rds_snapshot_monthly(size, aurora=aurora),
                    recommendation=_RECOMMENDATION,
                )
            )
    return findings
