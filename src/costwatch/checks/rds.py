from datetime import timedelta

import boto3

from costwatch import pricing
from costwatch.aws import client, tag_dict
from costwatch.metrics import daily_values
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
                    tags=tag_dict(snap.get("TagList")),
                    description=(
                        f"{size} GiB manual snapshot of {snap['Engine']} instance "
                        f"{snap['DBInstanceIdentifier']}, {(config.now - created).days} days old "
                        "(cost is an upper bound)"
                    ),
                    monthly_cost=pricing.rds_snapshot_monthly(region, size),
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
                    tags=tag_dict(snap.get("TagList")),
                    description=(
                        f"{size} GiB manual snapshot of {snap['Engine']} cluster "
                        f"{snap['DBClusterIdentifier']}, {(config.now - created).days} days old "
                        "(cost is an upper bound)"
                    ),
                    monthly_cost=pricing.rds_snapshot_monthly(region, size, aurora=aurora),
                    recommendation=_RECOMMENDATION,
                )
            )
    return findings


def idle_rds_instances(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    rds = client(session, "rds", region)
    cutoff = config.now - timedelta(days=config.idle_days)
    instances = [
        db
        for page in rds.get_paginator("describe_db_instances").paginate()
        for db in page["DBInstances"]
        if db.get("DBInstanceStatus") == "available"
        and db.get("InstanceCreateTime")
        and db["InstanceCreateTime"] <= cutoff
    ]
    if not instances:
        return []

    connections = daily_values(
        client(session, "cloudwatch", region),
        {
            db["DBInstanceIdentifier"]: (
                "AWS/RDS",
                "DatabaseConnections",
                {"DBInstanceIdentifier": db["DBInstanceIdentifier"]},
            )
            for db in instances
        },
        "Maximum",
        config.idle_days,
        config.now,
    )

    findings = []
    for db in instances:
        values = connections[db["DBInstanceIdentifier"]]
        if not values or max(values) > 0:
            continue
        aurora = db["Engine"].startswith("aurora")
        multi_az = db.get("MultiAZ", False)
        compute = pricing.rds_instance_monthly(
            region, db["DBInstanceClass"], db["Engine"], multi_az
        )
        # Aurora storage is billed per cluster, not per instance
        storage = (
            0.0
            if aurora
            else pricing.rds_storage_monthly(
                region, db.get("StorageType", "gp2"), db.get("AllocatedStorage", 0), multi_az
            )
        )
        price_note = "" if compute is not None else " (instance price unknown for this engine)"
        findings.append(
            Finding(
                check="idle-rds-instance",
                region=region,
                resource_id=db["DBInstanceArn"],
                name=db["DBInstanceIdentifier"],
                tags=tag_dict(db.get("TagList")),
                description=(
                    f"{db['DBInstanceClass']} {db['Engine']} had no connections in "
                    f"{config.idle_days} days{price_note}"
                ),
                monthly_cost=(compute or 0.0) + storage,
                recommendation=(
                    "Take a final snapshot and delete it, or stop it (RDS restarts stopped "
                    "instances after 7 days)."
                ),
            )
        )
    return findings


def retained_rds_backups(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    rds = client(session, "rds", region)
    findings = []
    paginator = rds.get_paginator("describe_db_instance_automated_backups")
    for page in paginator.paginate():
        for backup in page["DBInstanceAutomatedBackups"]:
            if backup.get("Status") != "retained":
                continue
            size = backup.get("AllocatedStorage", 0)
            identifier = backup["DBInstanceIdentifier"]
            findings.append(
                Finding(
                    check="retained-rds-backup",
                    region=region,
                    resource_id=backup.get("DBInstanceAutomatedBackupsArn")
                    or backup.get("DbiResourceId")
                    or identifier,
                    name=identifier,
                    description=(
                        f"{size} GiB of automated backups kept after instance {identifier} "
                        "was deleted (kept until their retention period ends)"
                    ),
                    monthly_cost=pricing.rds_snapshot_monthly(region, size),
                    recommendation=(
                        "Delete the retained backups if you won't restore from them "
                        "(take a manual snapshot first to keep one copy)."
                    ),
                )
            )
    return findings
