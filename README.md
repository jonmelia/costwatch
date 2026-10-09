# costwatch

[![CI](https://github.com/jonmelia/costwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/jonmelia/costwatch/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/costwatch.svg)](https://pypi.org/project/costwatch/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

**Find wasted AWS spend, and who left it running.**

costwatch scans an AWS account for idle and orphaned resources and prices each one with that
region's AWS list prices. It can also tell you who owns each resource (from tags or CloudTrail)
and whether it's managed by Terraform or CloudFormation, so you know who to ask and how to
remove it safely.

```
$ costwatch scan --region eu-west-2 --tfstate s3://my-tf-state/      # example output
Account 123456789012
┏━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ $/month ┃ Check                 ┃ Region    ┃ Resource              ┃ Details                                ┃ Managed by                    ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│   46.40 │ unattached-ebs-volume │ eu-west-2 │ old-db-data           │ 500 GiB gp3 volume not attached to any │ unmanaged                     │
│         │                       │           │ vol-65e02f208655ea11d │ instance (created 41 days ago)         │                               │
│   19.32 │ idle-load-balancer    │ eu-west-2 │ staging-alb           │ application load balancer has no       │ terraform                     │
│         │                       │           │                       │ registered targets                     │ module.staging.aws_lb.staging │
│    3.65 │ unused-elastic-ip     │ eu-west-2 │ eipalloc-b6c48e0af67… │ Elastic IP 18.130.4.21 is not          │ unmanaged                     │
│         │                       │           │                       │ associated with anything               │                               │
└─────────┴───────────────────────┴───────────┴───────────────────────┴────────────────────────────────────────┴───────────────────────────────┘
Total: ~$69.37/month (~$832/year) across 1 region(s)
Prices: AWS on-demand list prices of 2026-10-08.
```

- **Read-only.** It only calls `Describe*`, `List*`, `Get*` and `Lookup*` APIs.
  `costwatch policy` prints exactly what it needs.
- **Local.** It runs on your machine with your credentials. Nothing is sent anywhere.
- **Priced per region** from AWS's public price list, refreshed monthly.

## Install

Requires Python 3.12+.

```bash
uv tool install costwatch     # or: pipx install costwatch
uvx costwatch scan            # or run once without installing
```

## Quick start

```bash
costwatch scan                                  # every enabled region, default credentials
costwatch scan --profile prod --region eu-west-2 --region us-east-1
costwatch scan --owners                         # who owns each resource
costwatch scan --tfstate s3://my-tf-state/      # Terraform-managed or not
costwatch scan --format markdown -o waste.md    # for a PR, issue or wiki
costwatch checks                                # list the checks
costwatch policy                                # IAM policy for a read-only role
```

## Checks

| Check | Flags | Estimated monthly cost |
|---|---|---|
| `unattached-ebs-volume` | EBS volumes not attached to any instance | Storage + provisioned IOPS/throughput |
| `old-ebs-snapshot` | Snapshots older than 90 days that no AMI uses (AWS Backup/DLM snapshots excluded) | Snapshot storage (upper bound; snapshots are incremental) |
| `gp2-volume` | Attached gp2 volumes | The saving from moving to gp3 at the same performance |
| `unused-elastic-ip` | Elastic IPs not associated with anything | Public IPv4 hourly charge |
| `long-stopped-instance` | Instances stopped for 30+ days | Their attached EBS volumes |
| `idle-ec2-instance` | Running instances whose CPU never went above 5% in 14 days | Linux on-demand instance price |
| `unused-ami` | Your AMIs older than 90 days that no instance or launch template uses | Their snapshots (upper bound) |
| `idle-load-balancer` | ALB/NLB/GWLB/Classic with no targets, or no traffic in 14 days | Hourly load balancer charge |
| `idle-nat-gateway` | NAT gateways that sent under 1 MiB in 14 days | Hourly NAT gateway charge |
| `old-rds-snapshot` | Manual RDS/Aurora snapshots older than 90 days | Backup storage (upper bound) |
| `idle-rds-instance` | RDS instances with no connections in 14 days | Instance (by engine, class, Multi-AZ) + storage |
| `retained-rds-backup` | Automated backups kept after their instance was deleted | Backup storage (upper bound) |
| `log-group-no-retention` | CloudWatch log groups over 1 GiB with no retention set | Current log storage |

The idle checks use CloudWatch metrics and are deliberately conservative: an instance only counts
as idle if its CPU *never* went above 5%, and resources with no metric data are skipped (except
load balancers, which only publish metrics when they have traffic).

Tune them with `--snapshot-age-days`, `--stopped-days` and `--idle-days`, and pick checks with
`--checks a,b` or `--skip-checks a,b`.

## Prices

Each finding is priced with **its own region's** on-demand list prices: EBS by volume type,
IOPS and throughput; EC2 by instance type; RDS by engine, class and Multi-AZ; load balancers,
NAT gateways, snapshots and log storage. The prices ship inside the package, built from
AWS's public price list and refreshed monthly, so a scan needs no pricing permissions and makes
no pricing calls.

They're list prices: discounts, Savings Plans, Reserved Instances and the free tier aren't
applied. Regions without price data (rare; the report says which) use us-east-1 prices.

## Owners (`--owners`)

For each finding, costwatch looks for an owner in this order:

1. **Tags:** `Owner`, `CreatedBy`/`created-by`/`created_by`, `Contact`, `Team` (any case).
2. **CloudTrail:** the identity that created the resource. SSO users show as
   `user@example.com (PermissionSetRole)`, IAM users by name.

CloudTrail event history only goes back 90 days, so older untagged resources show as `unknown`.
Lookups are rate-limited by AWS (2 per second per region), so this is slower on accounts with
many findings.

## Terraform and CloudFormation (`--tfstate`)

Pass your Terraform state and every finding is marked **terraform** (with its address),
**cloudformation** (from the stack tag) or **unmanaged**:

```bash
costwatch scan --tfstate s3://my-tf-state/                 # every *.tfstate under the prefix, incl. workspaces
costwatch scan --tfstate s3://my-tf-state/prod/network.tfstate
costwatch scan --tfstate ./infra                           # every *.tfstate in a directory
terraform state pull > app.tfstate && costwatch scan --tfstate app.tfstate   # any other backend
```

- **Unmanaged and idle** usually means someone created it by hand and forgot it: the safest
  thing to clean up.
- **Managed and idle**: the recommendation changes to removing it from the code (e.g.
  `module.vpc.aws_nat_gateway.this["eu-west-2a"]`) and running `terraform apply`, because
  deleting it directly would cause drift.

Only managed resources count; `data` sources just read existing infrastructure. Volumes attached
through `aws_instance` block devices are matched too. Reading state from S3 needs `s3:ListBucket`
and `s3:GetObject` on the state bucket (plus `kms:Decrypt` for a customer-managed key). State
files can contain secrets: costwatch only reads identifier attributes, in memory, and never
prints anything else from them.

## Ignoring findings

Some waste is deliberate. To skip a resource:

- tag it `costwatch:ignore=true` (any value except `false`/`no`/`0`), or
- pass `--ignore <id, ARN or name>` (repeatable), or
- list IDs in a file, one per line (`#` for comments), and pass `--ignore-file .costwatchignore`.

The report says how many findings were ignored. `--min-cost 5` hides anything under $5/month.

## Output and CI

```bash
costwatch scan --format json  > findings.json   # schema_version 1; stable field names
costwatch scan --format csv   > findings.csv    # for spreadsheets
costwatch scan --format markdown -o waste.md    # for PR comments, issues, wikis
costwatch scan --fail-over 100                  # exit 1 if waste is $100/month or more
```

Exit codes: `0` scan finished, `1` waste at or over `--fail-over`, `2` error (credentials,
permissions for the whole scan, bad arguments). Problems with individual checks or regions, such
as a missing permission, are listed at the end of the report and don't stop the scan.

Example GitHub Actions job, using a read-only role via OIDC:

```yaml
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: arn:aws:iam::123456789012:role/costwatch-read
    aws-region: eu-west-2
- run: uvx costwatch scan --format markdown -o waste.md --fail-over 200
- run: cat waste.md >> "$GITHUB_STEP_SUMMARY"
  if: always()
```

## Other accounts

```bash
costwatch scan --role-arn arn:aws:iam::111122223333:role/costwatch-read --external-id <id>
```

Create the role in the target account with the policy from `costwatch policy`, trusting the
account you run costwatch from.

## Python API

```python
import boto3
from costwatch import ScanConfig, scan

result = scan(
    boto3.Session(profile_name="prod"), regions=["eu-west-2"], config=ScanConfig(idle_days=30)
)
for finding in result.findings:
    print(finding.check, finding.resource_id, round(finding.monthly_cost, 2))
```

## Development

```bash
git clone https://github.com/jonmelia/costwatch && cd costwatch
uv sync
uv run pytest --cov            # moto-based, no real AWS calls; 100% coverage required
uv run ruff check . && uv run ruff format . && uv run mypy
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

See [CONTRIBUTING.md](CONTRIBUTING.md) for adding a check, and [CHANGELOG.md](CHANGELOG.md) for
what's changed.

## License

[Apache 2.0](LICENSE)
