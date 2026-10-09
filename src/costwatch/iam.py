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
                "elasticloadbalancing:DescribeLoadBalancers",
                "elasticloadbalancing:DescribeTargetGroups",
                "elasticloadbalancing:DescribeTargetHealth",
            ],
            "Resource": "*",
        }
    ],
}


def policy_json() -> str:
    return json.dumps(POLICY, indent=2)
