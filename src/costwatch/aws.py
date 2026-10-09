import threading

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# boto3 sessions are not thread-safe when creating clients; the clients themselves are.
_client_lock = threading.Lock()

# Standard retries back off on throttling, which large accounts hit across many regions
_DEFAULT_CONFIG = Config(
    retries={"mode": "standard", "max_attempts": 8}, user_agent_extra="costwatch"
)


def client(session: boto3.Session, service: str, region: str, config: Config | None = None):
    merged = _DEFAULT_CONFIG.merge(config) if config else _DEFAULT_CONFIG
    with _client_lock:
        return session.client(service, region_name=region, config=merged)


def tag_dict(tags: list[dict] | None) -> dict[str, str]:
    return {t["Key"]: t.get("Value", "") for t in tags or [] if "Key" in t}


def name_tag(tags: list[dict] | None) -> str | None:
    return tag_dict(tags).get("Name")


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def short_error(e: Exception, limit: int = 160) -> str:
    """One-line error: 'AccessDenied: User ... is not authorized ...'."""
    if isinstance(e, ClientError):
        err = e.response.get("Error", {})
        code = err.get("Code") or str(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode"))
        text = f"{code}: {err.get('Message') or ''}".rstrip(": ")
    elif isinstance(e, BotoCoreError):
        text = str(e)
    else:
        text = f"{type(e).__name__}: {e}"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
