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
    assert pricing.gp2_to_gp3_monthly_savings(size) == pytest.approx(saving)


@pytest.mark.parametrize(
    ("instance_type", "monthly"),
    [
        ("t3.micro", 0.0832 / 8 * 730),
        ("m5.large", 0.096 * 730),
        ("m5.2xlarge", 0.096 * 4 * 730),
        ("r6g.xlarge", 0.1008 * 2 * 730),
        ("m5.metal", None),
        ("x9z.large", None),
    ],
)
def test_ec2_instance_monthly(instance_type, monthly):
    assert pricing.ec2_instance_monthly(instance_type) == (
        pytest.approx(monthly) if monthly else None
    )


def test_rds_instance_monthly_doubles_for_multi_az():
    single = pricing.rds_instance_monthly("db.t3.micro")
    assert single == pytest.approx(0.136 / 8 * 730)
    assert pricing.rds_instance_monthly("db.t3.micro", multi_az=True) == pytest.approx(2 * single)
