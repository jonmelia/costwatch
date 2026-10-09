# costwatch

[![CI](https://github.com/jonmelia/costwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/jonmelia/costwatch/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

**Find wasted AWS spend, and who left it running.**

costwatch scans an AWS account for idle and orphaned resources, estimates what each one costs
per month, and (with `--owners`) works out who owns it from tags or CloudTrail.

```
$ costwatch scan --owners          # example output, abridged
Account 123456789012
 $/month  Check                  Region     Resource       Details                                   Owner
   50.00  unattached-ebs-volume  eu-west-1  old-db-data    500 GiB gp2 volume not attached to any…   alice@example.com (AWSReservedSSO_Admin)
   16.43  idle-load-balancer     eu-west-1  staging-alb    application load balancer has no regis…   deploy-bot
    9.50  old-rds-snapshot       eu-west-1  pre-upgrade    100 GiB manual snapshot of postgres in…   dba-team  (tag:Team)
    3.65  unused-elastic-ip      us-east-1  eipalloc-…     Elastic IP 3.250.1.10 is not associated…  unknown

Waste by owner
  alice@example.com (AWSReservedSSO_Admin)   1   50.00
  deploy-bot                                 1   16.43
  ...
Total: ~$79.58/month (~$955/year) across 17 region(s)
```

- **Read-only.** It only calls `Describe*`/`Lookup*` APIs. Run `costwatch policy` to see
  exactly what it needs.
- **Local.** It runs on your machine with your credentials. Nothing is sent anywhere.

## Install

Requires Python 3.12+.

```bash
uv tool install git+https://github.com/jonmelia/costwatch
# or
pipx install git+https://github.com/jonmelia/costwatch
```

## Usage

```bash
costwatch scan                                  # all enabled regions, default credentials / $AWS_PROFILE
costwatch scan --profile prod --region eu-west-1 --region us-east-1
costwatch scan --owners                         # add who owns each resource
costwatch scan --min-cost 5                     # hide findings under $5/month
costwatch scan --snapshot-age-days 180 --stopped-days 60
costwatch scan --json > findings.json           # machine-readable
costwatch policy                                # IAM policy for a read-only role
```

If a check isn't permitted or a region is disabled, it's reported at the end and the rest of
the scan carries on.

## Checks

| Check | Flags | Estimated cost |
|---|---|---|
| `unattached-ebs-volume` | EBS volumes not attached to an instance | Storage + provisioned IOPS/throughput |
| `old-ebs-snapshot` | EBS snapshots older than 90 days that no AMI uses | Upper bound (snapshots are incremental) |
| `unused-elastic-ip` | Elastic IPs not associated with anything | $0.005/hour |
| `long-stopped-instance` | Instances stopped for 30+ days | Their attached EBS volumes |
| `idle-load-balancer` | ALB/NLB/GWLB/Classic with no registered targets | Hourly load balancer charge |
| `old-rds-snapshot` | Manual RDS/Aurora snapshots older than 90 days | Upper bound: allocated size × backup rate |

Prices are us-east-1 on-demand approximations ([`pricing.py`](src/costwatch/pricing.py)).
They're for ranking waste, not for matching your bill to the cent; other regions are usually
within about 20%.

## Owners (`--owners`)

For each finding, costwatch looks for an owner in this order:

1. **Tags:** `Owner`, `CreatedBy`/`created-by`/`created_by`, `Contact`, `Team` (any case).
2. **CloudTrail:** the identity that created the resource. SSO users show as
   `user@example.com (PermissionSetRole)`, IAM users by name.

CloudTrail event history only goes back 90 days, so older resources show as `unknown` unless
they're tagged. Lookups are rate-limited by AWS (2 per second per region), so this is slower
on accounts with lots of findings.

## Development

```bash
git clone https://github.com/jonmelia/costwatch && cd costwatch
uv sync
uv run pytest                                    # moto-based, no real AWS calls
uv run ruff check . && uv run ruff format .
```

`tests/e2e/` runs the real `costwatch` command against a [moto](https://github.com/getmoto/moto)
server. To try it by hand against a fake account full of waste:

```bash
uv run moto_server -p 5000
uv run python -m tests.e2e.seed --endpoint http://localhost:5000
export AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing
AWS_ENDPOINT_URL=http://localhost:5000 \
  uv run costwatch scan --region eu-west-1 --snapshot-age-days 0 --stopped-days 0 --min-cost 1
```

### Adding a check

1. Write a function `(session, region, config) -> list[Finding]` in
   [`src/costwatch/checks/`](src/costwatch/checks/) and add it to `ALL_CHECKS`.
2. Add any new permissions to [`iam.py`](src/costwatch/iam.py).
3. If it should support `--owners`, add its create event(s) to `CREATE_EVENTS` in
   [`owners.py`](src/costwatch/owners.py).
4. Test it with moto.

Contributions welcome: open an issue first for anything large.

## License

[Apache 2.0](LICENSE)
