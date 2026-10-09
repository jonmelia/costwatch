from datetime import timedelta

import boto3

from costwatch import pricing
from costwatch.aws import chunks, client, tag_dict
from costwatch.metrics import daily_values
from costwatch.models import Finding, ScanConfig

# Metric that shows real use, per load balancer type. Gateway LBs have no equivalent, so they
# are judged on registered targets alone. ELB only publishes these when there is traffic, so
# no data means no traffic.
_TRAFFIC_METRIC = {
    "application": ("AWS/ApplicationELB", "RequestCount"),
    "network": ("AWS/NetworkELB", "NewFlowCount"),
}


def _idle_reason(
    has_targets: bool, traffic: float | None, old_enough: bool, days: int
) -> str | None:
    if traffic:
        return None  # serving something, even if only redirects or fixed responses
    if not has_targets:
        quiet = f" and no traffic in {days} days" if traffic is not None and old_enough else ""
        return f"has no registered targets{quiet}"
    if traffic is not None and old_enough:
        return f"had no traffic in {days} days"
    return None


def idle_load_balancers(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    elbv2 = client(session, "elbv2", region)
    cutoff = config.now - timedelta(days=config.idle_days)

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

    metrics = {}
    for lb in load_balancers:
        if metric := _TRAFFIC_METRIC.get(lb.get("Type", "application")):
            # Dimension is the ARN suffix, e.g. app/my-alb/50dc6c495c0c9188
            dimension = lb["LoadBalancerArn"].split(":loadbalancer/", 1)[-1]
            metrics[lb["LoadBalancerArn"]] = (*metric, {"LoadBalancer": dimension})
    traffic = daily_values(
        client(session, "cloudwatch", region), metrics, "Sum", config.idle_days, config.now
    )

    tags = _elbv2_tags(elbv2, [lb["LoadBalancerArn"] for lb in load_balancers])
    findings = []
    for lb in load_balancers:
        arn = lb["LoadBalancerArn"]
        lb_type = lb.get("Type", "application")
        reason = _idle_reason(
            has_targets.get(arn, False),
            sum(traffic[arn]) if arn in traffic else None,
            lb["CreatedTime"] <= cutoff,
            config.idle_days,
        )
        if not reason:
            continue
        findings.append(
            Finding(
                check="idle-load-balancer",
                region=region,
                resource_id=arn,
                name=lb["LoadBalancerName"],
                tags=tags.get(arn, {}),
                description=f"{lb_type} load balancer {reason}",
                monthly_cost=pricing.load_balancer_monthly(region, lb_type),
                recommendation="Delete the load balancer if nothing is meant to use it.",
            )
        )
    return findings


def _elbv2_tags(elbv2, arns: list[str]) -> dict[str, dict[str, str]]:
    tags = {}
    for batch in chunks(arns, 20):
        for desc in elbv2.describe_tags(ResourceArns=batch)["TagDescriptions"]:
            tags[desc["ResourceArn"]] = tag_dict(desc.get("Tags"))
    return tags


def idle_classic_load_balancers(
    session: boto3.Session, region: str, config: ScanConfig
) -> list[Finding]:
    elb = client(session, "elb", region)
    cutoff = config.now - timedelta(days=config.idle_days)
    load_balancers = [
        lb
        for page in elb.get_paginator("describe_load_balancers").paginate()
        for lb in page["LoadBalancerDescriptions"]
    ]
    if not load_balancers:
        return []

    names = [lb["LoadBalancerName"] for lb in load_balancers]
    traffic = daily_values(
        client(session, "cloudwatch", region),
        {n: ("AWS/ELB", "RequestCount", {"LoadBalancerName": n}) for n in names},
        "Sum",
        config.idle_days,
        config.now,
    )
    tags = {}
    for batch in chunks(names, 20):
        for desc in elb.describe_tags(LoadBalancerNames=batch)["TagDescriptions"]:
            tags[desc["LoadBalancerName"]] = tag_dict(desc.get("Tags"))

    findings = []
    for lb in load_balancers:
        name = lb["LoadBalancerName"]
        reason = _idle_reason(
            bool(lb.get("Instances")),
            sum(traffic[name]),
            lb["CreatedTime"] <= cutoff,
            config.idle_days,
        )
        if not reason:
            continue
        findings.append(
            Finding(
                check="idle-load-balancer",
                region=region,
                resource_id=name,
                name=name,
                tags=tags.get(name, {}),
                description=f"Classic load balancer {reason}",
                monthly_cost=pricing.load_balancer_monthly(region, "classic"),
                recommendation="Delete it, or migrate to an ALB/NLB if still needed.",
            )
        )
    return findings
