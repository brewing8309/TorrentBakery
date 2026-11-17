from __future__ import annotations

import httpx


def gql(url: str, query: str, variables: dict, api_key: str = "", transport: httpx.BaseTransport | None = None) -> dict:
    """POST a GraphQL query (Stash and stash-box both take the key in an `ApiKey` header)."""
    headers = {"ApiKey": api_key} if api_key else {}
    with httpx.Client(transport=transport, timeout=30, headers=headers) as client:
        res = client.post(url, json={"query": query, "variables": variables})
    res.raise_for_status()
    body = res.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"][0]["message"])
    return body["data"]


def download(url: str, dest, api_key: str = "", transport: httpx.BaseTransport | None = None):
    """Fetch an image and store it as JPEG (hosts serve webp/png/jpg alike)."""
    import io

    from PIL import Image

    headers = {"ApiKey": api_key} if api_key else {}
    with httpx.Client(transport=transport, timeout=60, headers=headers, follow_redirects=True) as client:
        res = client.get(url)
    res.raise_for_status()
    Image.open(io.BytesIO(res.content)).convert("RGB").save(dest, quality=92)
    return dest
