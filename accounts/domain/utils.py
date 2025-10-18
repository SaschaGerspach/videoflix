def normalize_email(value: str) -> str:
    """Normalize email addresses by trimming whitespace and lowering case."""
    return (value or "").strip().lower()
