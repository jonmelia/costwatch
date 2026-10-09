from datetime import timedelta

import boto3

from costwatch import pricing
from costwatch.aws import client, name_tag, tag_dict
from costwatch.metrics import daily_values
from costwatch.models import Finding, ScanConfig

_IDLE_BYTES = 2**20  # under 1 MiB sent in the whole window


def idle_nat_gateways(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    cutoff = config.now - timedelta(days=config.idle_days)
    gateways = [
        nat
        for page in ec2.get_paginator("describe_nat_gateways").paginate(
            Filters=[{"Name": "state", "Values": ["available"]}]
        )
        for nat in page["NatGateways"]
        if nat["CreateTime"] <= cutoff
    ]
    if not gateways:
        return []

    sent = daily_values(
        client(session, "cloudwatch", region),
        {
            nat["NatGatewayId"]: (
                "AWS/NATGateway",
                "BytesOutToDestination",
                {"NatGatewayId": nat["NatGatewayId"]},
            )
            for nat in gateways
        },
        "Sum",
        config.idle_days,
        config.now,
    )

    findings = []
    for nat in gateways:
        values = sent[nat["NatGatewayId"]]
        if not values or sum(values) >= _IDLE_BYTES:
            continue
        findings.append(
            Finding(
                check="idle-nat-gateway",
                region=region,
                resource_id=nat["NatGatewayId"],
                name=name_tag(nat.get("Tags")),
                tags=tag_dict(nat.get("Tags")),
                description=(
                    f"NAT gateway sent {sum(values) / 1024:,.0f} KiB in {config.idle_days} days"
                ),
                monthly_cost=pricing.nat_gateway_monthly(region),
                recommendation=(
                    "Delete it if nothing in the private subnets needs outbound internet, "
                    "or use VPC endpoints instead."
                ),
            )
        )
    return findings
