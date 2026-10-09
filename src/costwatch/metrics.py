from datetime import datetime, timedelta

from costwatch.aws import chunks

_MAX_QUERIES = 500


def daily_values(
    cloudwatch,
    metrics: dict[str, tuple[str, str, dict[str, str]]],
    stat: str,
    days: int,
    now: datetime,
) -> dict[str, list[float]]:
    """Daily datapoints for many metrics at once.

    metrics maps a caller key to (namespace, metric name, dimensions). Returns key -> values;
    an empty list means CloudWatch has no data for that metric in the window.
    """
    results: dict[str, list[float]] = {key: [] for key in metrics}
    keys = list(metrics)
    for batch in chunks(keys, _MAX_QUERIES):
        ids = {f"m{i}": key for i, key in enumerate(batch)}
        queries = [
            {
                "Id": query_id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": metrics[key][0],
                        "MetricName": metrics[key][1],
                        "Dimensions": [
                            {"Name": name, "Value": value}
                            for name, value in metrics[key][2].items()
                        ],
                    },
                    "Period": 86400,
                    "Stat": stat,
                },
            }
            for query_id, key in ids.items()
        ]
        pages = cloudwatch.get_paginator("get_metric_data").paginate(
            MetricDataQueries=queries, StartTime=now - timedelta(days=days), EndTime=now
        )
        for page in pages:
            for result in page["MetricDataResults"]:
                results[ids[result["Id"]]].extend(result.get("Values", []))
    return results
