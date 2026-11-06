import math

import boto3
from botocore.exceptions import ClientError

from costwatch import pricing
from costwatch.aws import client
from costwatch.models import Finding, ScanConfig

_KINDS = {"RDS": "RDS instance", "Aurora": "Aurora cluster", "EBS": "EBS volume"}


def _source_exists(session: boto3.Session, arn: str) -> bool:
    """Whether the resource a recovery point came from still exists. Looked up in the source's
    own region: a copy in another region isn't a leftover just because its source is elsewhere."""
    parts = arn.split(":")
    region, kind, name = parts[3], parts[5], parts[-1]
    try:
        if kind == "db":
            client(session, "rds", region).describe_db_instances(DBInstanceIdentifier=name)
        elif kind == "cluster":
            client(session, "rds", region).describe_db_clusters(DBClusterIdentifier=name)
        else:
            volume = kind.removeprefix("volume/")
            client(session, "ec2", region).describe_volumes(VolumeIds=[volume])
    except ClientError as e:
        if "NotFound" in e.response["Error"]["Code"]:
            return False
        raise
    return True


def _size_gib(session: boto3.Session, region: str, point: dict) -> int:
    """AWS Backup reports 0 bytes for RDS and Aurora, so those ask RDS for the snapshot's size."""
    if size := point.get("BackupSizeInBytes"):
        return math.ceil(size / 2**30)
    rds = client(session, "rds", region)
    snapshot = ":".join(point["RecoveryPointArn"].split(":")[6:])
    if point["ResourceType"] == "Aurora":
        found = rds.describe_db_cluster_snapshots(DBClusterSnapshotIdentifier=snapshot)
        return found["DBClusterSnapshots"][0].get("AllocatedStorage", 0)
    found = rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot)
    return found["DBSnapshots"][0].get("AllocatedStorage", 0)


def _monthly(region: str, kind: str, size: int) -> float:
    if kind == "EBS":
        return pricing.snapshot_monthly(region, size)
    return pricing.rds_snapshot_monthly(region, size, aurora=kind == "Aurora")


def _finding(
    session: boto3.Session, region: str, vault: dict, source: str, points: list[dict]
) -> Finding:
    kind = points[0]["ResourceType"]
    name = source.split(":")[-1].removeprefix("volume/")
    newest = max(points, key=lambda p: p["CreationDate"])
    size = _size_gib(session, region, newest)
    expiries = [p.get("CalculatedLifecycle", {}).get("DeleteAt") for p in points]
    ends = (
        f"the last expires on {max(expiries):%Y-%m-%d}"
        if all(expiries)
        else "some are kept until deleted"
    )
    count = len(points)
    return Finding(
        check="orphaned-backup",
        region=region,
        resource_id=f"{vault['BackupVaultArn']}/{source}",
        name=name,
        description=(
            f"{count} AWS Backup recovery point{'' if count == 1 else 's'} in vault "
            f"{vault['BackupVaultName']} from deleted {_KINDS[kind]} {name}; {ends} (backups "
            f"are incremental, so this is priced as one full {size} GiB copy)"
        ),
        monthly_cost=_monthly(region, kind, size),
        recommendation=(
            "Let them expire, or delete them sooner if you won't restore from them. Check any "
            "vault lock first, and keep what your retention rules need."
        ),
    )


def orphaned_backups(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    backup = client(session, "backup", region)
    findings = []
    for page in backup.get_paginator("list_backup_vaults").paginate():
        for vault in page["BackupVaultList"]:
            account = vault["BackupVaultArn"].split(":")[4]
            by_source: dict[str, list[dict]] = {}
            pages = backup.get_paginator("list_recovery_points_by_backup_vault").paginate(
                BackupVaultName=vault["BackupVaultName"]
            )
            for points in pages:
                for point in points["RecoveryPoints"]:
                    source = point.get("ResourceArn", "")
                    # Another account's resources can't be seen, so they can't be called deleted
                    if (
                        point.get("Status") == "COMPLETED"
                        and point.get("ResourceType") in _KINDS
                        and source.split(":")[4:5] == [account]
                    ):
                        by_source.setdefault(source, []).append(point)
            findings += [
                _finding(session, region, vault, source, points)
                for source, points in by_source.items()
                if not _source_exists(session, source)
            ]
    return findings
