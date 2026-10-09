def ago(days: int) -> str:
    if days <= 0:
        return "today"
    return "1 day ago" if days == 1 else f"{days} days ago"
