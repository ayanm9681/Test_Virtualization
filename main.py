import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

load_dotenv()

MONGO_CONNECTION = os.getenv("MONGO_CONNECTION", "").strip(" '")
DB_NAME = os.getenv("DB_NAME", "TestRunner").strip(" '")
VIRTUAL_COLLECTION = os.getenv("VIRTUAL_COLLECTION", "virtual_backends").strip(" '")

app = FastAPI()
client: Optional[AsyncIOMotorClient] = None
collection = None
BASE_DIR = Path(__file__).resolve().parent
UI_HTML_PATH = BASE_DIR / "ui.html"


class VirtualAPI(BaseModel):
    api: str
    method: str = Field(..., pattern='^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)$', description='HTTP method')
    request_payload: Dict[str, Any]
    request_header: Dict[str, Any]
    response_payload: Dict[str, Any]
    response_header: Dict[str, Any]
    delay: int = Field(..., ge=0)


class VirtualAPIUpdate(BaseModel):
    method: Optional[str] = Field(None, pattern='^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)$')
    request_payload: Optional[Dict[str, Any]] = None
    request_header: Optional[Dict[str, Any]] = None
    response_payload: Optional[Dict[str, Any]] = None
    response_header: Optional[Dict[str, Any]] = None
    delay: Optional[int] = Field(None, ge=0)


@app.on_event("startup")
async def startup_db_client():
    global client, collection
    if not MONGO_CONNECTION:
        raise RuntimeError("MONGO_CONNECTION is not configured in environment variables")

    client = AsyncIOMotorClient(MONGO_CONNECTION)
    db = client[DB_NAME]
    collection = db[VIRTUAL_COLLECTION]
    await collection.create_index([("api", 1), ("method", 1)], unique=True)


@app.on_event("shutdown")
async def shutdown_db_client():
    if client:
        client.close()


@app.get("/", response_class=HTMLResponse)
async def root_ui():
    return await get_ui_html()


@app.get("/ui", response_class=HTMLResponse)
async def ui():
    return await get_ui_html()


async def get_ui_html() -> HTMLResponse:
    if not UI_HTML_PATH.exists():
        raise HTTPException(status_code=500, detail="UI file not found")
    return HTMLResponse(UI_HTML_PATH.read_text(encoding="utf-8"))


@app.post("/add_virtual_apis", status_code=201)
async def add_virtual_apis(payload: VirtualAPI):
    document = payload.dict()
    document["api"] = document["api"].strip()
    document["method"] = document["method"].strip().upper()
    try:
        await collection.insert_one(document)
        return {"message": "Virtual API added", "api": document["api"], "method": document["method"]}
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail=f"API '{document['api']}' with method '{document['method']}' already exists",
        )


@app.get("/virtual_apis")
async def get_virtual_api(api: str, method: str):
    document = await collection.find_one({"api": api, "method": method.strip().upper()}, {"_id": 0})
    if not document:
        raise HTTPException(status_code=404, detail=f"API '{api}' with method '{method}' not found")
    return document


@app.put("/virtual_apis")
async def update_virtual_api(api: str, method: str, update: VirtualAPIUpdate):
    update_data = {k: v for k, v in update.dict(exclude_unset=True).items()}
    if not update_data:
        raise HTTPException(status_code=400, detail="No update fields provided")
    if "method" in update_data:
        update_data["method"] = update_data["method"].strip().upper()

    result = await collection.update_one(
        {"api": api, "method": method.strip().upper()},
        {"$set": update_data},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail=f"API '{api}' with method '{method}' not found")
    return {"message": "Virtual API updated", "api": api, "method": method.strip().upper()}


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def handle_dynamic_api(full_path: str, request: Request):
    api_path = f"/{full_path}" if not full_path.startswith("/") else full_path
    method = request.method.strip().upper()
    document = await collection.find_one({"api": api_path, "method": method}, {"_id": 0})
    if not document:
        raise HTTPException(
            status_code=404,
            detail=f"API '{api_path}' not configured for method '{method}'",
        )

    delay = document.get("delay", 0)
    if delay and delay > 0:
        await asyncio.sleep(delay)

    return JSONResponse(content=document["response_payload"], headers=document["response_header"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=5006, reload=True)