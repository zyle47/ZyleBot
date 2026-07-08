import logging
from typing import Any

import httpx

from app.config import settings
from app.tools.base import RiskTier, tool

logger = logging.getLogger("zylebot.web_tools")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# Browser-like headers — some sites 403 requests that don't look like a browser.
# (Many big sites still block simple fetches behind bot-protection CDNs; the model
# falls back to other results / search snippets when a fetch fails.)
_BROWSER_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_MAX_DOWNLOAD_BYTES = 5_000_000  # don't pull down huge/binary responses


# --- Search providers ----------------------------------------------------

def _search_duckduckgo(query: str, max_results: int) -> list[dict[str, Any]]:
    from ddgs import DDGS

    with DDGS() as ddgs:
        raw = ddgs.text(query, max_results=max_results)
    return [
        {"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")}
        for r in raw
    ]


# Future key-based providers slot in here (see settings.brave_api_key / tavily_api_key).
_PROVIDERS = {
    "duckduckgo": _search_duckduckgo,
}


@tool(
    name="web_search",
    description=(
        "Search the web and return the top results (title, URL, snippet). Use this "
        "for current events, recent information, or any facts that may be past your "
        "training data. Follow up with fetch_url to read a result in full if needed."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "max_results": {
                "type": "integer",
                "description": "How many results to return (default 5, max 10).",
            },
        },
        "required": ["query"],
    },
    risk_tier=RiskTier.SAFE,
)
def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    provider = _PROVIDERS.get(settings.search_provider)
    if provider is None:
        return {"error": f"unknown search provider: {settings.search_provider}"}
    k = max(1, min(int(max_results), 10))
    try:
        results = provider(query, k)
    except Exception as exc:  # noqa: BLE001 - network/rate-limit/library errors
        logger.warning("web_search failed: %s", exc)
        return {"error": f"search failed: {exc}", "query": query}
    return {"query": query, "provider": settings.search_provider, "results": results}


# --- Page fetch + extraction ---------------------------------------------

@tool(
    name="fetch_url",
    description=(
        "Download a web page and return its main readable text (nav/ads/boilerplate "
        "stripped). Use after web_search to read a promising result in full. Returns "
        "cleaned text; you extract the specific facts you need from it."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The absolute http(s) URL to fetch."},
            "offset": {
                "type": "integer",
                "description": (
                    "Character offset to start from (default 0). Long pages are "
                    "returned in chunks; if a result says truncated, call again with "
                    "the next_offset value to continue reading the same page."
                ),
            },
        },
        "required": ["url"],
    },
    risk_tier=RiskTier.SAFE,
)
def fetch_url(url: str, offset: int = 0) -> dict[str, Any]:
    if not url.lower().startswith(("http://", "https://")):
        return {"error": "url must start with http:// or https://", "url": url}

    try:
        with httpx.Client(
            timeout=settings.web_request_timeout_s,
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type and "text" not in content_type:
                return {
                    "error": f"unsupported content-type: {content_type or 'unknown'}",
                    "url": url,
                }
            if len(resp.content) > _MAX_DOWNLOAD_BYTES:
                return {"error": "page too large to fetch", "url": url}
            html = resp.text
    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}", "url": url}
    except httpx.HTTPError as exc:
        return {"error": f"request failed: {exc}", "url": url}

    import trafilatura

    text = trafilatura.extract(html, include_comments=False, include_tables=True)
    if not text:
        # Fallback: strip tags with BeautifulSoup if trafilatura found no main content.
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = " ".join(soup.get_text(" ").split())
        except Exception:  # noqa: BLE001
            text = ""

    if not text:
        return {"error": "could not extract readable text", "url": url}

    total_chars = len(text)
    limit = settings.tool_max_fetch_chars
    offset = max(0, int(offset))
    chunk = text[offset : offset + limit]
    end = offset + len(chunk)
    truncated = end < total_chars

    result: dict[str, Any] = {
        "url": str(resp.url),
        "title": _extract_title(html),
        "total_chars": total_chars,
        "offset": offset,
        "returned_chars": len(chunk),
        "truncated": truncated,
        "text": chunk,
    }
    if truncated:
        # Tell the model exactly how to continue reading this same page.
        result["next_offset"] = end
        result["note"] = (
            f"Showing characters {offset}-{end} of {total_chars}. This page has more "
            f"content — to read the next part, call fetch_url again with the SAME url and "
            f"offset={end}. Do not re-fetch with the same offset."
        )
    return result


def _extract_title(html: str) -> str | None:
    try:
        import trafilatura

        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            return meta.title
    except Exception:  # noqa: BLE001
        pass
    return None


# --- Weather (Open-Meteo, no API key) ------------------------------------

# WMO weather interpretation codes -> human text (Open-Meteo `weather_code`).
_WMO_CODES = {
    0: "clear sky",
    1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


@tool(
    name="get_weather",
    description=(
        "Get the current weather for a place by name (city, town, or 'city, country'). "
        "Returns temperature, feels-like, humidity, wind, and conditions. Prefer this "
        "over web_search for any weather question — it is accurate and needs one call."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "Place name, e.g. 'Novi Sad' or 'Belgrade, Serbia'.",
            }
        },
        "required": ["location"],
    },
    risk_tier=RiskTier.SAFE,
)
def get_weather(location: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=settings.web_request_timeout_s) as client:
            geo = client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1},
            ).json()
            results = geo.get("results")
            if not results:
                return {"error": f"could not find location: {location}"}
            loc = results[0]

            wx = client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": loc["latitude"],
                    "longitude": loc["longitude"],
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "wind_speed_10m,weather_code",
                },
            ).json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {"error": f"weather lookup failed: {exc}", "location": location}

    cur = wx.get("current", {})
    code = cur.get("weather_code")
    place = ", ".join(
        p for p in (loc.get("name"), loc.get("admin1"), loc.get("country")) if p
    )
    return {
        "location": place,
        "temperature_c": cur.get("temperature_2m"),
        "feels_like_c": cur.get("apparent_temperature"),
        "humidity_percent": cur.get("relative_humidity_2m"),
        "wind_speed_kmh": cur.get("wind_speed_10m"),
        "conditions": _WMO_CODES.get(code, f"code {code}"),
        "observed_at": cur.get("time"),
    }
