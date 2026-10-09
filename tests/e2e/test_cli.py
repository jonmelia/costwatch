"""Runs the installed costwatch CLI as a subprocess against a real moto HTTP server."""

import json
import os
import subprocess

import pytest
from moto.server import ThreadedMotoServer

from tests.e2e.seed import DEFAULT_REGION, seed


@pytest.fixture(scope="module")
def endpoint():
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture(scope="module")
def seeded(endpoint):
    return seed(endpoint)


def run_cli(endpoint: str, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "AWS_ENDPOINT_URL": endpoint,
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": DEFAULT_REGION,
    }
    env.pop("AWS_PROFILE", None)
    return subprocess.run(
        ["costwatch", *args], env=env, capture_output=True, text=True, timeout=120
    )


def test_scan_finds_every_kind_of_waste(endpoint, seeded):
    proc = run_cli(
        endpoint,
        "scan",
        "--region",
        DEFAULT_REGION,
        "--snapshot-age-days",
        "0",
        "--stopped-days",
        "0",
        "--json",
    )

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["errors"] == []
    found = {f["resource_id"] for f in report["findings"]} | {
        f["name"] for f in report["findings"] if f["name"]
    }
    missing = {check: rid for check, rid in seeded.items() if rid not in found}
    assert not missing, f"not detected: {missing}"


def test_table_output(endpoint, seeded):
    proc = run_cli(endpoint, "scan", "--region", DEFAULT_REGION)

    assert proc.returncode == 0, proc.stderr
    assert "Total:" in proc.stdout
