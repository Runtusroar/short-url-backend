def build_public_short_url(
    domain: str,
    short_code: str,
    *,
    scheme: str,
    port: int | None,
) -> str:
    """Build a user-facing short URL from validated deployment components."""
    authority = f"{domain}:{port}" if port is not None else domain
    return f"{scheme}://{authority}/{short_code}"
