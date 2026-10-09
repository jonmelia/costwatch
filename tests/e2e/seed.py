"""Create one of every kind of waste costwatch detects, in a moto server.

    uv run moto_server -p 5000
    uv run python -m tests.e2e.seed --endpoint http://localhost:5000
    export AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing
    AWS_ENDPOINT_URL=http://localhost:5000 \\
        uv run costwatch scan --region eu-west-1 --snapshot-age-days 0 --stopped-days 0 --min-cost 1

The age flags are 0 because everything is created just now. --min-cost 1 hides the ~1,000
small snapshots moto pre-creates behind its default AMIs.
"""

import argparse
import json

import boto3

DEFAULT_REGION = "eu-west-1"


def seed(endpoint: str, region: str = DEFAULT_REGION) -> dict[str, str]:
    """Create the resources and return {check name: resource id or name}."""
    session = boto3.Session(
        aws_access_key_id="testing", aws_secret_access_key="testing", region_name=region
    )
    ec2 = session.client("ec2", endpoint_url=endpoint)
    elb = session.client("elb", endpoint_url=endpoint)
    elbv2 = session.client("elbv2", endpoint_url=endpoint)
    rds = session.client("rds", endpoint_url=endpoint)
    az = f"{region}a"

    volume = ec2.create_volume(AvailabilityZone=az, Size=500, VolumeType="gp2")["VolumeId"]
    snapshot = ec2.create_snapshot(VolumeId=volume)["SnapshotId"]
    eip = ec2.allocate_address(Domain="vpc")["AllocationId"]

    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    instance = ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1)["Instances"][0]["InstanceId"]
    ec2.stop_instances(InstanceIds=[instance])
    gp2 = ec2.create_volume(AvailabilityZone=az, Size=200, VolumeType="gp2")["VolumeId"]
    running = ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1)["Instances"][0]["InstanceId"]
    ec2.attach_volume(VolumeId=gp2, InstanceId=running, Device="/dev/sdf")
    unused_ami = ec2.register_image(
        Name="old-golden-image",
        RootDeviceName="/dev/xvda",
        BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 30}}],
    )["ImageId"]

    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnets = [
        ec2.create_subnet(VpcId=vpc, CidrBlock=cidr, AvailabilityZone=f"{region}{zone}")["Subnet"][
            "SubnetId"
        ]
        for cidr, zone in (("10.0.1.0/24", "a"), ("10.0.2.0/24", "b"))
    ]
    alb = elbv2.create_load_balancer(Name="idle-alb", Subnets=subnets)["LoadBalancers"][0]
    elb.create_load_balancer(
        LoadBalancerName="idle-clb",
        Listeners=[{"Protocol": "HTTP", "LoadBalancerPort": 80, "InstancePort": 80}],
        AvailabilityZones=[az],
    )

    rds.create_db_instance(
        DBInstanceIdentifier="app-db",
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        AllocatedStorage=100,
        MasterUsername="admin",
        MasterUserPassword="password123",
    )
    rds.create_db_snapshot(DBInstanceIdentifier="app-db", DBSnapshotIdentifier="app-db-manual")
    rds.create_db_instance(
        DBInstanceIdentifier="deleted-db",
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        AllocatedStorage=20,
        MasterUsername="admin",
        MasterUserPassword="password123",
        BackupRetentionPeriod=7,
    )
    rds.delete_db_instance(
        DBInstanceIdentifier="deleted-db", SkipFinalSnapshot=True, DeleteAutomatedBackups=False
    )
    rds.create_db_cluster(
        DBClusterIdentifier="app-cluster",
        Engine="aurora-postgresql",
        MasterUsername="admin",
        MasterUserPassword="password123",
    )
    rds.create_db_cluster_snapshot(
        DBClusterIdentifier="app-cluster", DBClusterSnapshotIdentifier="app-cluster-manual"
    )

    return {
        "unattached-ebs-volume": volume,
        "old-ebs-snapshot": snapshot,
        "unused-elastic-ip": eip,
        "gp2-volume": gp2,
        "unused-ami": unused_ami,
        "retained-rds-backup": "deleted-db",
        "long-stopped-instance": instance,
        "idle-load-balancer": alb["LoadBalancerArn"],
        "idle-classic-load-balancer": "idle-clb",
        "old-rds-snapshot": "app-db-manual",
        "old-rds-cluster-snapshot": "app-cluster-manual",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--endpoint", default="http://localhost:5000")
    parser.add_argument("--region", default=DEFAULT_REGION)
    args = parser.parse_args()
    print(json.dumps(seed(args.endpoint, args.region), indent=2))
