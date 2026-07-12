import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

XAI_API_KEY = os.environ.get("XAI_API_KEY")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4-1-fast-reasoning")
XAI_API_URL = "https://api.x.ai/v1/responses"


def _extract_response_text(data: dict) -> str:
    parts: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if block.get("type") == "output_text" and block.get("text"):
                parts.append(block["text"])
    text = "\n\n".join(parts).strip()
    if text:
        return text

    # Fallback for unexpected response shapes.
    for item in data.get("output") or []:
        if item.get("type") == "message" and isinstance(item.get("content"), str):
            return item["content"].strip()

    raise ValueError("Empty response from xAI")


def ask_observation_likelihood(
    scientific_name: Optional[str],
    lat: float,
    lon: float,
    aphiaid: Optional[int] = None,
) -> str:
    if not XAI_API_KEY:
        raise ValueError("XAI_API_KEY is not configured")

    species = scientific_name or (f"AphiaID {aphiaid}" if aphiaid else "this species")
    prompt = (
        f"How likely is it to observe {species} at latitude {lat} and longitude {lon}?\n\n"
        "Search the web for distribution data, occurrence records, and relevant literature. "
        "Assess ecological plausibility, known geographic range, habitat preferences, and "
        "important caveats.\n\n"
        "Format your answer in markdown. Include inline markdown links to sources, especially "
        "peer-reviewed papers, GBIF/OBIS/WoRMS pages, and other authoritative references."
    )

    response = requests.post(
        XAI_API_URL,
        headers={
            "Authorization": f"Bearer {XAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": XAI_MODEL,
            "input": [{"role": "user", "content": prompt}],
            "tools": [{"type": "web_search"}],
            "max_output_tokens": 2000,
        },
        timeout=120,
    )
    response.raise_for_status()
    return _extract_response_text(response.json())
