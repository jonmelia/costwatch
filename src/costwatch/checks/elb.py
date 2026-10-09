import boto3

from costwatch import pricing
from costwatch.aws import client
from costwatch.models import Finding, ScanConfig


def idle_load_balancers(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    elbv2 = client(session, "elbv2", region)

    load_balancers = [
        lb
        for page in elbv2.get_paginator("describe_load_balancers").paginate()
        for lb in page["LoadBalancers"]
    ]
    if not load_balancers:
        return []

    # Load balancer ARN -> whether any of its target groups has a registered target
    has_targets: dict[str, bool] = {}
    for page in elbv2.get_paginator("describe_target_groups").paginate():
        for tg in page["TargetGroups"]:
            if not tg.get("LoadBalancerArns"):
                continue
            registered = bool(
                elbv2.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])[
                    "TargetHealthDescriptions"
                ]
            )
            for lb_arn in tg["LoadBalancerArns"]:
                has_targets[lb_arn] = has_targets.get(lb_arn, False) or registered

    findings = []
    for lb in load_balancers:
        if has_targets.get(lb["LoadBalancerArn"]):
            continue
        lb_type = lb.get("Type", "application")
        findings.append(
            Finding(
                check="idle-load-balancer",
                region=region,
                resource_id=lb["LoadBalancerArn"],
                name=lb["LoadBalancerName"],
                description=f"{lb_type} load balancer has no registered targets",
                monthly_cost=pricing.load_balancer_monthly(lb_type),
                recommendation="Delete the load balancer if nothing is meant to use it.",
            )
        )
    return findings


def idle_classic_load_balancers(
    session: boto3.Session, region: str, config: ScanConfig
) -> list[Finding]:
    elb = client(session, "elb", region)
    findings = []
    for page in elb.get_paginator("describe_load_balancers").paginate():
        for lb in page["LoadBalancerDescriptions"]:
            if lb.get("Instances"):
                continue
            findings.append(
                Finding(
                    check="idle-load-balancer",
                    region=region,
                    resource_id=lb["LoadBalancerName"],
                    name=lb["LoadBalancerName"],
                    description="Classic load balancer has no registered instances",
                    monthly_cost=pricing.load_balancer_monthly("classic"),
                    recommendation="Delete it, or migrate to an ALB/NLB if still needed.",
                )
            )
    return findings
