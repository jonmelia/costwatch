"""The read-only permissions costwatch needs. This becomes the customer's cross-account role."""

import json

POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "CostwatchReadOnly",
            "Effect": "Allow",
            "Action": [
                "sts:GetCallerIdentity",
                "ec2:DescribeRegions",
                "ec2:DescribeVolumes",
                "ec2:DescribeSnapshots",
                "ec2:DescribeImages",
                "ec2:DescribeAddresses",
                "ec2:DescribeInstances",
                "ec2:DescribeNatGateways",
                "ec2:DescribeLaunchTemplates",
                "ec2:DescribeLaunchTemplateVersions",
                "autoscaling:DescribeLaunchConfigurations",
                "elasticloadbalancing:DescribeLoadBalancers",
                "elasticloadbalancing:DescribeTargetGroups",
                "elasticloadbalancing:DescribeTargetHealth",
                "elasticloadbalancing:DescribeTags",
                "rds:DescribeDBSnapshots",
                "rds:DescribeDBClusterSnapshots",
                "rds:DescribeDBInstances",
                "rds:DescribeDBInstanceAutomatedBackups",
                "cloudwatch:GetMetricData",
                "logs:DescribeLogGroups",
                "cloudtrail:LookupEvents",
            ],
            "Resource": "*",
        }
    ],
}


def policy_json() -> str:
    return json.dumps(POLICY, indent=2)
