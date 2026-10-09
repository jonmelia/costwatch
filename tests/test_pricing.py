import gzip
import json
from importlib.resources import files

import pytest

from costwatch import pricing


@pytest.mark.parametrize(
    ("size", "saving"),
    [
        (100, 2.0),  # $10 gp2 vs $8 gp3
        (200, 4.0),  # bursts to 250 MB/s but doesn't sustain it, so baseline gp3 matches
        (2000, 20.0),  # $200 gp2 vs $160 + 3,000 extra IOPS ($15) + 125 MB/s ($5)
    ],
)
def test_gp2_to_gp3_savings(size, saving):
    assert pricing.gp2_to_gp3_monthly_savings("us-east-1", size) == pytest.approx(saving)


def test_prices_differ_by_region():
    assert pricing.ebs_volume_monthly("us-east-1", "gp3", 100) == pytest.approx(8.0)
    assert pricing.ebs_volume_monthly("eu-west-2", "gp3", 100) == pytest.approx(9.28)
    assert pricing.ec2_instance_monthly("eu-west-2", "m5.large") == pytest.approx(0.111 * 730)


def test_unknown_region_falls_back_to_us_east_1():
    assert not pricing.has_region("xx-nowhere-1")
    assert pricing.ebs_volume_monthly("xx-nowhere-1", "gp3", 100) == pytest.approx(8.0)


def test_missing_price_in_region_falls_back_to_default():
    # The fixture's eu-west-2 has no gateway LB price, like the real price list
    data = pricing._data()
    del data["regions"]["eu-west-2"]["lb_hour"]["gateway"]
    assert pricing.load_balancer_monthly("eu-west-2", "gateway") == pytest.approx(0.0125 * 730)


def test_ec2_instance_monthly():
    assert pricing.ec2_instance_monthly("us-east-1", "m5.2xlarge") == pytest.approx(0.384 * 730)
    assert pricing.ec2_instance_monthly("us-east-1", "x9z.large") is None


def test_rds_instance_monthly():
    single = pricing.rds_instance_monthly("us-east-1", "db.t3.micro", "postgres")
    assert single == pytest.approx(0.018 * 730)
    multi = pricing.rds_instance_monthly("us-east-1", "db.t3.micro", "postgres", multi_az=True)
    assert multi == pytest.approx(0.036 * 730)
    assert pricing.rds_instance_monthly("us-east-1", "db.t3.micro", "oracle-ee") is None


def test_rds_storage_doubles_for_multi_az():
    assert pricing.rds_storage_monthly("us-east-1", "gp2", 100) == pytest.approx(11.5)
    assert pricing.rds_storage_monthly("us-east-1", "gp2", 100, multi_az=True) == pytest.approx(
        23.0
    )


def test_archived_snapshot_is_cheaper():
    assert pricing.snapshot_monthly("us-east-1", 100, archived=True) == pytest.approx(1.25)
    assert pricing.snapshot_monthly("us-east-1", 100) == pytest.approx(5.0)


def test_bundled_price_file_is_complete():
    pricing.use_data(None)
    data = json.loads(
        gzip.decompress((files("costwatch") / "data" / "prices.json.gz").read_bytes())
    )

    assert len(data["regions"]) >= 25
    for region, prices in data["regions"].items():
        assert 0.05 < prices["ebs_gb_month"]["gp3"] < 0.2, region
        assert 0.02 < prices["snapshot_gb_month"] < 0.1, region
        assert len(prices["ec2_hour"]) > 100, region
    assert data["regions"]["us-east-1"]["ec2_hour"]["m5.large"] == pytest.approx(0.096)
