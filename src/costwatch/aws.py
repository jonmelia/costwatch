import threading

import boto3

# boto3 sessions are not thread-safe when creating clients; the clients themselves are.
_client_lock = threading.Lock()


def client(session: boto3.Session, service: str, region: str):
    with _client_lock:
        return session.client(service, region_name=region)


def name_tag(tags: list[dict] | None) -> str | None:
    for tag in tags or []:
        if tag.get("Key") == "Name":
            return tag.get("Value")
    return None


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
