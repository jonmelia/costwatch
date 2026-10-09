import pytest

from costwatch.checks.rds import old_rds_cluster_snapshots, old_rds_snapshots
from tests.conftest import REGION


@pytest.fixture
def rds(session):
    return session.client("rds", region_name=REGION)


@pytest.fixture
def db_instance(rds):
    rds.create_db_instance(
        DBInstanceIdentifier="app-db",
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        AllocatedStorage=100,
        MasterUsername="admin",
        MasterUserPassword="password123",
    )
    return "app-db"


@pytest.fixture
def db_cluster(rds):
    rds.create_db_cluster(
        DBClusterIdentifier="app-cluster",
        Engine="aurora-postgresql",
        MasterUsername="admin",
        MasterUserPassword="password123",
    )
    return "app-cluster"


def test_old_manual_snapshot_is_flagged_with_cost(session, rds, db_instance, future_config):
    rds.create_db_snapshot(DBInstanceIdentifier=db_instance, DBSnapshotIdentifier="pre-upgrade")

    findings = old_rds_snapshots(session, REGION, future_config)

    assert [f.name for f in findings] == ["pre-upgrade"]
    assert findings[0].check == "old-rds-snapshot"
    assert findings[0].monthly_cost == pytest.approx(9.5)  # 100 GiB * $0.095


def test_recent_manual_snapshot_is_not_flagged(session, rds, db_instance, config):
    rds.create_db_snapshot(DBInstanceIdentifier=db_instance, DBSnapshotIdentifier="pre-upgrade")

    assert old_rds_snapshots(session, REGION, config) == []


def test_automated_snapshots_are_not_flagged(session, rds, db_instance, future_config):
    # The instance has an automated snapshot but no manual ones
    assert old_rds_snapshots(session, REGION, future_config) == []


def test_old_manual_cluster_snapshot_is_flagged(session, rds, db_cluster, future_config):
    rds.create_db_cluster_snapshot(
        DBClusterIdentifier=db_cluster, DBClusterSnapshotIdentifier="cluster-pre-upgrade"
    )

    findings = old_rds_cluster_snapshots(session, REGION, future_config)

    assert [f.name for f in findings] == ["cluster-pre-upgrade"]


def test_recent_manual_cluster_snapshot_is_not_flagged(session, rds, db_cluster, config):
    rds.create_db_cluster_snapshot(
        DBClusterIdentifier=db_cluster, DBClusterSnapshotIdentifier="cluster-pre-upgrade"
    )

    assert old_rds_cluster_snapshots(session, REGION, config) == []
