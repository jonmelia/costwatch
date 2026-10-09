# costwatch

Find wasted AWS spend: idle and orphaned resources, each with an estimated monthly cost.

```
$ costwatch scan
Account 123456789012
  $/month  Check                  Region     Resource      Details
    90.00  unattached-ebs-volume  eu-west-1  vol-60b7...   200 GiB io1 volume not attached to any instance
    16.43  idle-load-balancer     eu-west-1  staging-alb   application load balancer has no registered targets
     3.65  unused-elastic-ip      eu-west-1  eipalloc-...  Elastic IP 3.250.1.10 is not associated with anything
Total: ~$110.08/month (~$1,321/year) across 17 region(s)
```

## Checks

| Check | Flags | Estimated cost |
|---|---|---|
| `unattached-ebs-volume` | EBS volumes not attached to an instance | Storage + provisioned IOPS/throughput |
| `old-ebs-snapshot` | Snapshots older than 90 days not used by an AMI | Upper bound (snapshots are incremental) |
| `unused-elastic-ip` | Elastic IPs not associated with anything | $0.005/hour |
| `long-stopped-instance` | Instances stopped for 30+ days | Their attached EBS volumes |
| `idle-load-balancer` | ALB/NLB/GWLB/Classic with no registered targets | Hourly LB charge |

Prices are us-east-1 on-demand approximations (`src/costwatch/pricing.py`), good for ranking
waste rather than matching your bill to the cent.

## Usage

```bash
uv sync
uv run costwatch scan                       # all enabled regions, uses $AWS_PROFILE
uv run costwatch scan --profile dev --region eu-west-1 --region us-east-1
uv run costwatch scan --min-cost 5 --json   # machine-readable
uv run costwatch policy                     # read-only IAM policy it needs
```

## Development

```bash
uv run pytest          # tests use moto, no real AWS calls
uv run ruff check . && uv run ruff format .
```

To add a check, write a function `(session, region, config) -> list[Finding]` in
`src/costwatch/checks/`, add it to `ALL_CHECKS`, add its permissions to `iam.py`, and test it
with moto.
