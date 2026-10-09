from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from costwatch.models import ScanConfig

REGION = "us-east-1"


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    for var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture
def session():
    with mock_aws():
        yield boto3.Session(region_name=REGION)


@pytest.fixture
def ec2(session):
    return session.client("ec2", region_name=REGION)


@pytest.fixture
def config():
    return ScanConfig()


@pytest.fixture
def future_config():
    """A clock 120 days ahead, so resources created 'now' look old."""
    return ScanConfig(now=datetime.now(UTC) + timedelta(days=120))


@pytest.fixture
def ami_id(ec2):
    return ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]


@pytest.fixture
def preexisting_snapshots(ec2):
    """moto seeds the account with snapshots behind its default AMIs; tests ignore those."""
    pages = ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"])
    return {s["SnapshotId"] for page in pages for s in page["Snapshots"]}
