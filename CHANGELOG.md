# Changelog

All notable changes to costwatch. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/): the CLI options, exit codes, check IDs and JSON
output (`schema_version`) are the public interface.

## [1.0.2] - 2026-10-09

### Changed
- Prices updated from the AWS price list published 2026-10-08.

## [1.0.1] - 2026-10-09

### Fixed
- `costwatch policy` was missing `elasticloadbalancing:DescribeTags`, so the
  `idle-load-balancer` check failed with AccessDenied under a role built from it. A new test
  records every AWS call a scan makes and fails if the policy doesn't allow it.

## [1.0.0] - 2026-10-09

### Added
- **Regional prices.** Every finding is priced with its region's AWS on-demand list prices
  (EC2 by instance type, RDS by engine/class/Multi-AZ, EBS, snapshots, load balancers, NAT
  gateways, log storage), bundled with the package and refreshed monthly from the public AWS
  price list. Reports show the price list date.
- **`--tfstate`**: mark findings as Terraform-managed (with the resource address),
  CloudFormation-managed or unmanaged. Reads local files, directories, S3 objects and S3
  prefixes including workspaces. Managed findings get a "remove it from the code"
  recommendation.
- Seven checks: `idle-ec2-instance`, `idle-rds-instance`, `idle-nat-gateway`, `gp2-volume`,
  `unused-ami`, `retained-rds-backup`, `log-group-no-retention`.
- `--checks` / `--skip-checks` and `costwatch checks`.
- Ignoring findings with the `costwatch:ignore` tag, `--ignore` or `--ignore-file`.
- `--format table|json|csv|markdown`, `--output`, and `--fail-over` for CI (exit code 1).
- `--role-arn` / `--external-id` to scan another account.
- JSON output gains `schema_version`, `costwatch_version`, `generated_at`, `ignored`,
  `prices_as_of`.
- A small Python API: `from costwatch import scan, ScanConfig`, and `python -m costwatch`.

### Changed
- `idle-load-balancer` also uses CloudWatch traffic: load balancers with targets but no traffic
  are flagged, and ones serving traffic without targets (e.g. HTTPS redirects) are not.
- `old-ebs-snapshot` skips snapshots managed by AWS Backup or Data Lifecycle Manager and prices
  archive-tier snapshots correctly.
- gp2 volumes on long-stopped instances are no longer counted twice.
- Errors are shortened to the AWS error code and message; an unexpected error in one check no
  longer stops the scan. AWS calls retry with backoff when throttled.
- 100% test coverage, enforced in CI, plus type checking with mypy.

## [0.1.0] - 2026-10-09

### Added
- First release: `scan` with checks for unattached EBS volumes, old EBS snapshots, unused
  Elastic IPs, long-stopped instances, idle load balancers and old manual RDS snapshots.
- `--owners`: owners from tags, then the CloudTrail creator.
- `policy` command printing the read-only IAM policy.

[1.0.2]: https://github.com/jonmelia/costwatch/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/jonmelia/costwatch/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/jonmelia/costwatch/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/jonmelia/costwatch/releases/tag/v0.1.0
