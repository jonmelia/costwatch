import copy
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from costwatch import pricing
from costwatch.models import ScanConfig

REGION = "us-east-1"


def _fixture_prices() -> dict:
    """Fixed prices so tests don't change when the bundled price list is refreshed."""
    us_east_1 = copy.deepcopy(pricing.DEFAULTS)
    us_east_1["ec2_hour"] = {"t3.micro": 0.0104, "m5.large": 0.096, "m5.2xlarge": 0.384}
    us_east_1["rds_hour"] = {"PostgreSQL": {"db.t3.micro": [0.018, 0.036]}}
    eu_west_2 = copy.deepcopy(us_east_1)
    eu_west_2["ebs_gb_month"]["gp3"] = 0.0928
    eu_west_2["ec2_hour"]["m5.large"] = 0.111
    return {
        "publication_date": "2026-01-01T00:00:00Z",
        "regions": {"us-east-1": us_east_1, "eu-west-2": eu_west_2},
    }


@pytest.fixture(autouse=True)
def fixed_prices():
    pricing.use_data(_fixture_prices())
    yield
    pricing.use_data(None)


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


def put_daily(cloudwatch, namespace, metric, dimensions: dict, values, end):
    """One datapoint per day, ending the day before `end`."""
    cloudwatch.put_metric_data(
        Namespace=namespace,
        MetricData=[
            {
                "MetricName": metric,
                "Dimensions": [{"Name": k, "Value": v} for k, v in dimensions.items()],
                "Value": value,
                "Timestamp": end - timedelta(days=i + 1),
            }
            for i, value in enumerate(values)
        ],
    )


@pytest.fixture
def cloudwatch(session):
    return session.client("cloudwatch", region_name=REGION)
