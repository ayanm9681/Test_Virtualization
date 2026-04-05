import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

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
LOCAL_VIRTUAL_APIS_FILE = os.getenv("LOCAL_VIRTUAL_APIS_FILE", "virtual_apis.json").strip(" '")

app = FastAPI()
client: Optional[AsyncIOMotorClient] = None
collection = None
BASE_DIR = Path(__file__).resolve().parent
UI_HTML_PATH = BASE_DIR / "ui.html"
LOCAL_VIRTUAL_APIS_PATH = BASE_DIR / LOCAL_VIRTUAL_APIS_FILE

STORAGE_DB = "db"
STORAGE_LOCAL = "local"
STORAGE_AUTO = "auto"


def normalize_storage(storage: Optional[str]) -> str:
    if not storage:
        return STORAGE_AUTO
    storage_key = storage.strip().lower()
    if storage_key in ("db", "database"):
        return STORAGE_DB
    if storage_key in ("local", "json", "file"):
        return STORAGE_LOCAL
    return STORAGE_AUTO


async def load_local_documents() -> List[Dict[str, Any]]:
    if not LOCAL_VIRTUAL_APIS_PATH.exists():
        return []
    try:
        text = await asyncio.to_thread(LOCAL_VIRTUAL_APIS_PATH.read_text, encoding="utf-8")
        data = json.loads(text or "[]")
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return data


async def save_local_documents(documents: List[Dict[str, Any]]) -> None:
    await asyncio.to_thread(LOCAL_VIRTUAL_APIS_PATH.write_text, json.dumps(documents, indent=2), encoding="utf-8")


async def find_local_document(api: str, method: str) -> Optional[Dict[str, Any]]:
    documents = await load_local_documents()
    for document in documents:
        if document.get("api") == api and document.get("method") == method:
            return document
    return None


async def add_local_document(document: Dict[str, Any]) -> None:
    documents = await load_local_documents()
    if any(doc.get("api") == document["api"] and doc.get("method") == document["method"] for doc in documents):
        raise DuplicateKeyError("Duplicate API entry")
    documents.append(document)
    await save_local_documents(documents)


async def update_local_document(api: str, method: str, update_data: Dict[str, Any]) -> int:
    documents = await load_local_documents()
    for index, document in enumerate(documents):
        if document.get("api") == api and document.get("method") == method:
            updated_document = {**document, **update_data}
            if updated_document["method"] != method and any(
                doc.get("api") == api and doc.get("method") == updated_document["method"] for doc in documents
                if doc is not document
            ):
                raise DuplicateKeyError("Duplicate API entry")
            documents[index] = updated_document
            await save_local_documents(documents)
            return 1
    return 0


async def list_local_documents() -> List[Dict[str, Any]]:
    return await load_local_documents()


async def get_document(api: str, method: str, storage: Optional[str] = None) -> Optional[Dict[str, Any]]:
    storage_key = normalize_storage(storage)
    normalized_method = method.strip().upper()
    if storage_key in (STORAGE_DB, STORAGE_AUTO) and collection is not None:
        document = await collection.find_one({"api": api, "method": normalized_method}, {"_id": 0})
        if document:
            return document
    if storage_key in (STORAGE_LOCAL, STORAGE_AUTO):
        return await find_local_document(api, normalized_method)
    return None


async def add_document(document: Dict[str, Any], storage: Optional[str] = None) -> None:
    storage_key = normalize_storage(storage)
    if storage_key in (STORAGE_DB, STORAGE_AUTO) and collection is not None:
        try:
            await collection.insert_one(document)
            return
        except DuplicateKeyError:
            raise
    if storage_key in (STORAGE_LOCAL, STORAGE_AUTO):
        await add_local_document(document)
        return
    raise HTTPException(status_code=400, detail="Invalid storage option")


async def update_document(api: str, method: str, update_data: Dict[str, Any], storage: Optional[str] = None) -> int:
    storage_key = normalize_storage(storage)
    normalized_method = method.strip().upper()
    if storage_key in (STORAGE_DB, STORAGE_AUTO) and collection is not None:
        try:
            result = await collection.update_one(
                {"api": api, "method": normalized_method},
                {"$set": update_data},
            )
            if result.matched_count > 0:
                return result.matched_count
        except DuplicateKeyError:
            raise
    if storage_key in (STORAGE_LOCAL, STORAGE_AUTO):
        return await update_local_document(api, normalized_method, update_data)
    return 0


async def list_documents(storage: Optional[str] = None) -> List[Dict[str, Any]]:
    storage_key = normalize_storage(storage)
    items: List[Dict[str, Any]] = []
    if storage_key in (STORAGE_DB, STORAGE_AUTO) and collection is not None:
        cursor = collection.find({}, {"_id": 0})
        async for document in cursor:
            items.append({**document, "storage": "database"})
    if storage_key in (STORAGE_LOCAL, STORAGE_AUTO):
        for document in await list_local_documents():
            items.append({**document, "storage": "local"})
    return items


async def ensure_database_collection() -> None:
    if collection is not None:
        await collection.create_index([("api", 1), ("method", 1)], unique=True)


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
    if MONGO_CONNECTION:
        client = AsyncIOMotorClient(MONGO_CONNECTION)
        db = client[DB_NAME]
        collection = db[VIRTUAL_COLLECTION]
        await ensure_database_collection()


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
async def add_virtual_apis(payload: VirtualAPI, storage: Optional[str] = STORAGE_AUTO):
    document = payload.dict()
    document["api"] = document["api"].strip()
    document["method"] = document["method"].strip().upper()
    try:
        await add_document(document, storage)
        return {"message": "Virtual API added", "api": document["api"], "method": document["method"]}
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail=f"API '{document['api']}' with method '{document['method']}' already exists",
        )


@app.get("/virtual_apis")
async def get_virtual_api(api: str, method: str, storage: Optional[str] = STORAGE_AUTO):
    document = await get_document(api.strip(), method, storage)
    if not document:
        raise HTTPException(status_code=404, detail=f"API '{api}' with method '{method}' not found")
    return document


@app.put("/virtual_apis")
async def update_virtual_api(api: str, method: str, update: VirtualAPIUpdate, storage: Optional[str] = STORAGE_AUTO):
    update_data = {k: v for k, v in update.dict(exclude_unset=True).items()}
    if not update_data:
        raise HTTPException(status_code=400, detail="No update fields provided")
    if "method" in update_data:
        update_data["method"] = update_data["method"].strip().upper()

    try:
        updated_count = await update_document(api.strip(), method, update_data, storage)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail=f"API '{api}' with method '{method}' already exists with the new method",
        )
    if updated_count == 0:
        raise HTTPException(status_code=404, detail=f"API '{api}' with method '{method}' not found")
    return {"message": "Virtual API updated", "api": api, "method": method.strip().upper()}


@app.get("/virtual_apis/list")
async def list_virtual_apis(storage: Optional[str] = STORAGE_AUTO):
    return await list_documents(storage)


class VirtualAPITestRequest(BaseModel):
    api: str
    method: str = Field(..., pattern='^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)$', description='HTTP method')
    storage: Optional[str] = STORAGE_AUTO
    request_payload: Optional[Dict[str, Any]] = None
    request_header: Optional[Dict[str, Any]] = None


@app.post("/test_virtual_api")
async def test_virtual_api(payload: VirtualAPITestRequest):
    api = payload.api.strip()
    method = payload.method.strip().upper()
    document = await get_document(api, method, payload.storage)
    if not document:
        raise HTTPException(status_code=404, detail=f"API '{api}' with method '{method}' not found")

    delay = document.get("delay", 0)
    if delay and delay > 0:
        await asyncio.sleep(delay)

    return JSONResponse(content=document["response_payload"], headers=document["response_header"])


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def handle_dynamic_api(full_path: str, request: Request):
    api_path = f"/{full_path}" if not full_path.startswith("/") else full_path
    method = request.method.strip().upper()
    document = await get_document(api_path, method)
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