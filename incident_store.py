import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
INCIDENTS_FILE = os.getenv("INCIDENTS_FILE", "incidents.json").strip(" '")
INCIDENTS_PATH = BASE_DIR / INCIDENTS_FILE

INCIDENT_PREFIX = "INC-"
INCIDENT_START = 1001


class IncidentAPIEntry(BaseModel):
    api: str
    method: Optional[str] = None
    error: Dict[str, Any]


class IncidentCreateRequest(BaseModel):
    test_time: str
    apis: List[IncidentAPIEntry] = Field(..., min_length=1)


async def _load_incidents() -> List[Dict[str, Any]]:
    if not INCIDENTS_PATH.exists():
        return []
    try:
        text = await asyncio.to_thread(INCIDENTS_PATH.read_text, encoding="utf-8")
        data = json.loads(text or "[]")
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return data


async def _save_incidents(incidents: List[Dict[str, Any]]) -> None:
    await asyncio.to_thread(INCIDENTS_PATH.write_text, json.dumps(incidents, indent=2), encoding="utf-8")


def _next_incident_number(incidents: List[Dict[str, Any]]) -> str:
    max_seq = INCIDENT_START - 1
    for record in incidents:
        number = record.get("incident_number", "")
        if number.startswith(INCIDENT_PREFIX):
            suffix = number[len(INCIDENT_PREFIX):]
            if suffix.isdigit():
                max_seq = max(max_seq, int(suffix))
    return f"{INCIDENT_PREFIX}{max_seq + 1}"


async def create_incident(payload: IncidentCreateRequest) -> Dict[str, Any]:
    incidents = await _load_incidents()
    record = {
        "incident_number": _next_incident_number(incidents),
        "test_time": payload.test_time,
        "apis": [entry.dict() for entry in payload.apis],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    incidents.append(record)
    await _save_incidents(incidents)
    return record


async def get_incident(incident_number: str) -> Optional[Dict[str, Any]]:
    incidents = await _load_incidents()
    for record in incidents:
        if record.get("incident_number") == incident_number:
            return record
    return None
