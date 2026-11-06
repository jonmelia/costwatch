from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from costwatch.checks import backup as backup_check
from costwatch.checks.backup import orphaned_backups
from costwatch.checks.rds import recent_orphaned_rds_snapshots
from tests.conftest import REGION

ACCOUNT = "123456789012"
VAULT_ARN = f"arn:aws:backup:{REGION}:{ACCOUNT}:backup-vault:main"
GONE_DB = f"arn:aws:rds:{REGION}:{ACCOUNT}:db:old-db"


class FakeBackup:
    """moto has no recovery points, so the two listing calls are answered here."""

    def __init__(self, points):
        self.points = points

    def get_paginator(self, name):
        return self

    def paginate(self, **kwargs):
        if kwargs:
            return [{"RecoveryPoints": self.points}]
        return [{"BackupVaultList": [{"BackupVaultArn": VAULT_ARN, "BackupVaultName": "main"}]}]


@pytest.fixture
def rds(session):
    return session.client("rds", region_name=REGION)


@pytest.fixture
def recovery_points(monkeypatch):
    real = backup_check.client

    def use(points):
        fake = FakeBackup(points)
        monkeypatch.setattr(
            backup_check,
            "client",
            lambda s, service, region, config=None: (
                fake if service == "backup" else real(s, service, region)
            ),
        )

    return use


def point(source=GONE_DB, kind="RDS", days=1, size=2 * 2**30, status="COMPLETED", deleted=True):
    created = datetime(2026, 11, 1, tzinfo=UTC) + timedelta(days=days)
    lifecycle = {"DeleteAt": created + timedelta(days=35)} if deleted else {}
    return {
        "RecoveryPointArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:snapshot:awsbackup:job-{days}",
        "ResourceArn": source,
        "ResourceType": kind,
        "Status": status,
        "CreationDate": created,
        "BackupSizeInBytes": size,
        "CalculatedLifecycle": lifecycle,
    }


def create_db(rds, name):
    rds.create_db_instance(
        DBInstanceIdentifier=name,
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        AllocatedStorage=100,
        MasterUsername="admin",
        MasterUserPassword="password123",
    )


def create_cluster(rds, name):
    rds.create_db_cluster(
        DBClusterIdentifier=name,
        Engine="aurora-postgresql",
        MasterUsername="admin",
        MasterUserPassword="password123",
    )


def test_recovery_points_of_a_deleted_database_are_one_finding(session, recovery_points, config):
    recovery_points([point(days=1), point(days=2)])

    findings = orphaned_backups(session, REGION, config)

    assert len(findings) == 1
    f = findings[0]
    assert f.check == "orphaned-backup" and f.name == "old-db"
    assert "2 AWS Backup recovery points" in f.description
    assert "the last expires on 2026-12-08" in f.description
    assert f.monthly_cost == pytest.approx(2 * 0.095)


def test_recovery_points_of_a_live_database_are_left_alone(session, rds, recovery_points, config):
    create_db(rds, "old-db")
    recovery_points([point()])

    assert orphaned_backups(session, REGION, config) == []


def test_a_copy_looks_for_its_source_in_the_source_region(session, recovery_points, config):
    create_db(session.client("rds", region_name="eu-west-2"), "old-db")
    recovery_points([point(source=f"arn:aws:rds:eu-west-2:{ACCOUNT}:db:old-db")])

    assert orphaned_backups(session, REGION, config) == []


def test_another_accounts_source_is_not_called_deleted(session, recovery_points, config):
    recovery_points([point(source=f"arn:aws:rds:{REGION}:999999999999:db:old-db")])

    assert orphaned_backups(session, REGION, config) == []


def test_unfinished_and_unknown_kinds_are_skipped(session, recovery_points, config):
    recovery_points([point(status="PARTIAL"), point(kind="DynamoDB")])

    assert orphaned_backups(session, REGION, config) == []


def test_deleted_volume_is_priced_as_a_snapshot(session, recovery_points, config):
    source = f"arn:aws:ec2:{REGION}:{ACCOUNT}:volume/vol-0123456789abcdef0"
    recovery_points([point(source=source, kind="EBS", deleted=False)])

    findings = orphaned_backups(session, REGION, config)

    assert findings[0].name == "vol-0123456789abcdef0"
    assert "some are kept until deleted" in findings[0].description
    assert findings[0].monthly_cost == pytest.approx(2 * 0.05)


def test_a_failed_lookup_is_an_error_not_a_deleted_source(session, monkeypatch):
    class Denied:
        def describe_db_instances(self, **kwargs):
            error = {"Error": {"Code": "AccessDenied", "Message": "no"}}
            raise ClientError(error, "DescribeDBInstances")

    monkeypatch.setattr(backup_check, "client", lambda *a, **k: Denied())

    with pytest.raises(ClientError):
        backup_check._source_exists(session, GONE_DB)


def test_rds_size_comes_from_the_snapshot(session, rds, recovery_points, config):
    create_db(rds, "other-db")
    rds.create_db_snapshot(DBInstanceIdentifier="other-db", DBSnapshotIdentifier="backup-one")
    arn = f"arn:aws:rds:{REGION}:{ACCOUNT}:snapshot:backup-one"
    recovery_points([{**point(size=0), "RecoveryPointArn": arn}])

    findings = orphaned_backups(session, REGION, config)

    assert "one full 100 GiB copy" in findings[0].description


def test_aurora_size_comes_from_the_cluster_snapshot(session, rds, recovery_points, config):
    create_cluster(rds, "live-cluster")
    rds.create_db_cluster_snapshot(
        DBClusterIdentifier="live-cluster", DBClusterSnapshotIdentifier="backup-two"
    )
    source = f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster:gone-cluster"
    arn = f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster-snapshot:backup-two"
    recovery_points([{**point(source=source, kind="Aurora", size=0), "RecoveryPointArn": arn}])

    findings = orphaned_backups(session, REGION, config)

    assert findings[0].name == "gone-cluster"


def test_recent_snapshot_of_a_deleted_database_is_flagged(session, rds, config):
    create_db(rds, "old-db")
    rds.create_db_snapshot(DBInstanceIdentifier="old-db", DBSnapshotIdentifier="final")
    rds.delete_db_instance(DBInstanceIdentifier="old-db", SkipFinalSnapshot=True)

    findings = recent_orphaned_rds_snapshots(session, REGION, config)

    assert [f.name for f in findings] == ["final"]
    assert findings[0].check == "orphaned-rds-snapshot"
    assert "of deleted instance old-db" in findings[0].description


def test_recent_snapshot_of_a_live_database_is_left_alone(session, rds, config):
    create_db(rds, "app-db")
    rds.create_db_snapshot(DBInstanceIdentifier="app-db", DBSnapshotIdentifier="pre-upgrade")

    assert recent_orphaned_rds_snapshots(session, REGION, config) == []


def test_old_snapshots_stay_with_the_old_snapshot_check(session, rds, future_config):
    create_db(rds, "old-db")
    rds.create_db_snapshot(DBInstanceIdentifier="old-db", DBSnapshotIdentifier="final")
    rds.delete_db_instance(DBInstanceIdentifier="old-db", SkipFinalSnapshot=True)

    assert recent_orphaned_rds_snapshots(session, REGION, future_config) == []


def test_recent_snapshot_of_a_deleted_cluster_is_flagged(session, rds, config):
    create_cluster(rds, "old-cluster")
    rds.create_db_cluster_snapshot(
        DBClusterIdentifier="old-cluster", DBClusterSnapshotIdentifier="cluster-final"
    )
    rds.delete_db_cluster(DBClusterIdentifier="old-cluster", SkipFinalSnapshot=True)

    findings = recent_orphaned_rds_snapshots(session, REGION, config)

    assert [f.name for f in findings] == ["cluster-final"]
    assert "of deleted cluster old-cluster" in findings[0].description
