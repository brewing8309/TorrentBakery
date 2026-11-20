from __future__ import annotations

import httpx

from ..net import keep_on_site


def gql(url: str, query: str, variables: dict, api_key: str = "", transport: httpx.BaseTransport | None = None) -> dict:
    """POST a GraphQL query (Stash and stash-box both take the key in an `ApiKey` header)."""
    headers = {"ApiKey": api_key} if api_key else {}
    with httpx.Client(transport=transport, timeout=30, headers=headers) as client:
        res = client.post(url, json={"query": query, "variables": variables})
    try:
        body = res.json()
    except ValueError:
        body = {}
    # Stash/stash-box answer an invalid query with HTTP 400 *and* the reason in the body: show the reason, not just the status
    if body.get("errors"):
        raise RuntimeError(body["errors"][0]["message"])
    res.raise_for_status()
    return body["data"]


def download_raw(url: str, dest, api_key: str = "", transport: httpx.BaseTransport | None = None):
    """Fetch a file as it is (animated images must not be re-encoded)."""
    headers = {"ApiKey": api_key} if api_key else {}
    with httpx.Client(transport=transport, timeout=120, headers=headers, follow_redirects=True, event_hooks=keep_on_site(url, "ApiKey")) as client:
        res = client.get(url)
    res.raise_for_status()
    dest.write_bytes(res.content)
    return dest


def download(url: str, dest, api_key: str = "", transport: httpx.BaseTransport | None = None):
    """Fetch an image and store it as JPEG (hosts serve webp/png/jpg alike)."""
    import io

    from PIL import Image

    headers = {"ApiKey": api_key} if api_key else {}
    with httpx.Client(transport=transport, timeout=60, headers=headers, follow_redirects=True, event_hooks=keep_on_site(url, "ApiKey")) as client:
        res = client.get(url)
    res.raise_for_status()
    Image.open(io.BytesIO(res.content)).convert("RGB").save(dest, quality=92)
    return dest
