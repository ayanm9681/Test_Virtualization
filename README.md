# Virtual API Builder

A lightweight **API virtualisation / mock server** built with FastAPI. It lets you register fake HTTP endpoints with configurable request/response payloads, headers, and artificial delay — then serve them as real HTTP routes or test them from the built-in browser UI.

Intended as a **showcase / demo** of the concept. See [Architectural Trade-offs & Production Scaling](#architectural-trade-offs--production-scaling) for known limitations and what a production-grade version would look like.

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Reference](#api-reference)
  - [Add a Virtual API](#add-a-virtual-api)
  - [Retrieve a Virtual API](#retrieve-a-virtual-api)
  - [Update a Virtual API](#update-a-virtual-api)
  - [List All Virtual APIs](#list-all-virtual-apis)
  - [Test a Virtual API](#test-a-virtual-api)
  - [Dynamic Route Catch-all](#dynamic-route-catch-all)
- [Storage Backends](#storage-backends)
- [Browser UI](#browser-ui)
- [Project Structure](#project-structure)
- [Architectural Trade-offs & Production Scaling](#architectural-trade-offs--production-scaling)

---

## Features

| Feature | Description |
|---|---|
| **Virtual endpoint registration** | Register any path + HTTP method combination as a mock endpoint |
| **Full HTTP method support** | GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD |
| **Configurable responses** | Custom response payload (JSON) and response headers per endpoint |
| **Request capture** | Store expected request payload and headers alongside each mock |
| **Artificial delay** | Per-endpoint configurable delay in seconds to simulate latency |
| **Dual storage backends** | Persist mocks in a local JSON file **or** a MongoDB collection |
| **Auto storage routing** | `auto` mode queries MongoDB first, falls back to local JSON |
| **Dynamic catch-all routing** | Any unregistered path is intercepted and served from the mock store |
| **Duplicate detection** | Enforces unique (api path, method) pairs with 409 responses |
| **Browser UI** | Built-in single-page management interface at `/` or `/ui` |
| **Async I/O** | Fully async FastAPI + Motor stack; no blocking I/O on the hot path |

---

## Quick Start

### 1. Clone & install

```bash
git clone <repo-url>
cd Test_Virtualization
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env   # then edit as needed
```

> If no `.env` is present the server starts in **local JSON** mode automatically.

### 3. Run

```bash
python main.py
# or via uvicorn directly
uvicorn main:app --host 127.0.0.1 --port 5006 --reload
```

Open [http://localhost:5006](http://localhost:5006) in your browser.

---

## Configuration

All configuration is via environment variables (`.env` file or shell export).

| Variable | Default | Description |
|---|---|---|
| `MONGO_CONNECTION` | *(empty)* | MongoDB connection string. When empty, MongoDB is disabled and only local JSON storage is used. |
| `DB_NAME` | `TestRunner` | MongoDB database name |
| `VIRTUAL_COLLECTION` | `virtual_backends` | MongoDB collection name for virtual API documents |
| `LOCAL_VIRTUAL_APIS_FILE` | `virtual_apis.json` | Filename for the local JSON storage file (created in the project root) |

### `.env.example`

```dotenv
MONGO_CONNECTION=mongodb://localhost:27017
DB_NAME=TestRunner
VIRTUAL_COLLECTION=virtual_backends
LOCAL_VIRTUAL_APIS_FILE=virtual_apis.json
```

---

## API Reference

### Data Model

Every virtual API entry has the following shape:

```json
{
  "api": "/my/endpoint",
  "method": "POST",
  "request_payload": { "key": "value" },
  "request_header": { "Authorization": "Bearer <token>" },
  "response_payload": { "status": "ok" },
  "response_header": { "Content-Type": "application/json" },
  "delay": 0
}
```

| Field | Type | Notes |
|---|---|---|
| `api` | string | The URL path to mock, e.g. `/users/profile` |
| `method` | string | One of `GET POST PUT DELETE PATCH OPTIONS HEAD` |
| `request_payload` | object | Expected/documented request body (JSON) |
| `request_header` | object | Expected/documented request headers |
| `response_payload` | object | The JSON body returned when this endpoint is hit |
| `response_header` | object | Headers added to the mocked response |
| `delay` | integer ≥ 0 | Artificial response delay in **seconds** |

---

### Add a Virtual API

```
POST /add_virtual_apis?storage=<local|db|auto>
Content-Type: application/json
```

**Body:** full `VirtualAPI` object (see data model above).

**Responses:**

| Status | Meaning |
|---|---|
| `201 Created` | Mock registered successfully |
| `409 Conflict` | An entry for this `(api, method)` already exists |
| `422 Unprocessable Entity` | Validation error (invalid method, missing fields, etc.) |

**Example:**

```bash
curl -X POST "http://localhost:5006/add_virtual_apis?storage=local" \
  -H "Content-Type: application/json" \
  -d '{
    "api": "/users/me",
    "method": "GET",
    "request_payload": {},
    "request_header": {},
    "response_payload": {"id": 1, "name": "Alice"},
    "response_header": {},
    "delay": 0
  }'
```

---

### Retrieve a Virtual API

```
GET /virtual_apis?api=<path>&method=<METHOD>&storage=<local|db|auto>
```

**Responses:** `200 OK` with the document, or `404` if not found.

```bash
curl "http://localhost:5006/virtual_apis?api=/users/me&method=GET&storage=local"
```

---

### Update a Virtual API

```
PUT /virtual_apis?api=<path>&method=<METHOD>&storage=<local|db|auto>
Content-Type: application/json
```

**Body:** `VirtualAPIUpdate` — all fields are optional; only supplied fields are changed.

```json
{
  "response_payload": { "id": 1, "name": "Bob" },
  "delay": 2
}
```

**Responses:** `200 OK`, `404` not found, `409` duplicate on method rename.

---

### List All Virtual APIs

```
GET /virtual_apis/list?storage=<local|db|auto>
```

Returns an array of all stored documents. Each item includes a `"storage"` field indicating where it lives (`"database"` or `"local"`).

---

### Test a Virtual API

```
POST /test_virtual_api
Content-Type: application/json
```

**Body:**

```json
{
  "api": "/users/me",
  "method": "GET",
  "storage": "local",
  "request_payload": {}
}
```

The server looks up the mock, waits `delay` seconds if set, then returns the stored `response_payload` with the stored `response_header`.

---

### Dynamic Route Catch-all

Any HTTP request to a path that is not one of the management endpoints above is intercepted by a wildcard route. The server looks up `(path, method)` in the mock store and returns the configured response — exactly as if it were a real backend.

```bash
# After registering GET /users/me above, this works directly:
curl http://localhost:5006/users/me
# → {"id": 1, "name": "Alice"}
```

---

## Storage Backends

The `storage` query parameter controls where data is read from and written to.

| Value | Behaviour |
|---|---|
| `local` / `json` / `file` | Reads and writes the local JSON file only |
| `db` / `database` | Reads and writes MongoDB only; errors if no connection is configured |
| `auto` *(default)* | Tries MongoDB first (if connected); falls back to local JSON on read. On write, prefers MongoDB when available |

The local JSON file (`virtual_apis.json` by default) is created automatically on the first write. It is **gitignored** so it does not pollute version control.

---

## Browser UI

Navigate to [http://localhost:5006](http://localhost:5006) or [http://localhost:5006/ui](http://localhost:5006/ui).

The single-page interface has three panels accessible from the left sidebar:

| Panel | Purpose |
|---|---|
| **Add Virtual API** | Form to register a new mock endpoint |
| **Retrieve / Update API** | Load an existing mock, edit its fields, and save changes |
| **Test Virtual API** | Send a test request to a registered mock and inspect the response body, headers, and status |

The UI communicates with the same REST endpoints described above.

---

## Project Structure

```
Test_Virtualization/
├── main.py               # FastAPI application — routes, storage logic, data models
├── ui.html               # Single-file browser UI (served by FastAPI)
├── requirements.txt      # Python dependencies
├── .env                  # Local environment config (gitignored)
├── .env.example          # Template for .env
├── virtual_apis.json     # Auto-created local JSON store (gitignored)
└── README.md
```

---

## Architectural Trade-offs & Production Scaling

> This project is a **lite showcase** — it demonstrates the core idea with minimal dependencies and a single-file backend. The following sections are honest about where it cuts corners and what a production-grade implementation would require.

---

### 1. Local JSON file is not safe for concurrent writes

**Problem:** `virtual_apis.json` is read, mutated in memory, and written back in full on every add/update. Two simultaneous requests can produce a race condition: the second writer overwrites the first's changes because both read the old state before either writes.

**Production remedy:** Replace the file with an actual database (Postgres, MongoDB, Redis). If a flat-file store is a hard requirement, use a file lock (`asyncio.Lock` or `fcntl`/`msvcrt`) to serialise writes, or switch to SQLite with WAL mode enabled.

---

### 2. No authentication or authorisation

**Problem:** Any client on the network can add, update, or delete virtual APIs. The management endpoints (`/add_virtual_apis`, `/virtual_apis`, etc.) are completely open.

**Production remedy:** Add an API key header check or OAuth2/JWT middleware. Separate read vs. write permissions. Restrict management endpoints to an internal network or admin role.

---

### 3. No request matching — all responses are static

**Problem:** The mock always returns the same `response_payload` regardless of the incoming request body or headers. Real-world testing often requires conditional responses (e.g. return 404 for unknown user IDs, 401 for missing tokens).

**Production remedy:** Introduce a response rules engine — store an ordered list of matchers (JSONPath predicates, header checks) against responses per endpoint. Tools like WireMock and Mockoon solve this problem; the architecture would need a `rules` array in the document schema and a matching loop at request time.

---

### 4. Delay is a hard `asyncio.sleep`, blocking the event loop slot

**Problem:** `await asyncio.sleep(delay)` holds the coroutine for the full delay duration. Under high concurrency with long delays, this consumes many concurrent tasks and memory.

**Production remedy:** For realistic load simulation, the delay should be bounded (e.g. max 30 s), and very long delays should use a queue-based approach (enqueue the response, deliver it after the timer). Rate-limit the delay parameter in the API.

---

### 5. No persistence across restarts when using local JSON — and no migration strategy

**Problem:** The local JSON file is a flat list. As the number of mocks grows there is no indexing, no schema versioning, and no migration tooling. A corrupt file means total data loss.

**Production remedy:** Use a proper database with schema migrations (Alembic for SQLAlchemy, MongoDB schema validation). Add export/import endpoints so teams can check mocks into version control as structured files.

---

### 6. Auto storage mode has ambiguous semantics

**Problem:** In `auto` mode the write goes to MongoDB if it is available, but reads fall through to local JSON if MongoDB returns nothing. This means a mock could exist in local JSON but be invisible when MongoDB is connected, leading to confusing behaviour.

**Production remedy:** Remove the `auto` mode or make it explicit — merge results from both stores and surface the source clearly, or enforce a single canonical store at startup time and remove the fallback logic.

---

### 7. No observability — logs, metrics, or tracing

**Problem:** There is no structured logging, no request ID propagation, and no metrics endpoint. Debugging why a test failed against a mock is difficult in production.

**Production remedy:** Add structured JSON logging (structlog or Python's `logging` with a JSON formatter), emit OpenTelemetry traces, and expose a `/metrics` endpoint (Prometheus) with counters for mock hits per endpoint.

---

### 8. Single process, no horizontal scaling

**Problem:** The app is designed to run as a single `uvicorn` process. The local JSON file store is inherently single-machine; even the MongoDB path has no caching layer.

**Production remedy:** Containerise with Docker, run behind a load balancer (nginx / Traefik), and ensure all state is in MongoDB (remove local JSON from the write path). Add a Redis cache in front of MongoDB for high-volume read-heavy workloads.

---

### 9. The catch-all route shadows future real routes

**Problem:** The wildcard `/{full_path:path}` route intercepts every unmatched request. Adding new management endpoints later risks being silently caught by this route if the path isn't carefully ordered in FastAPI's router.

**Production remedy:** Namespace all management endpoints under `/api/v1/` and serve mock responses only under a separate prefix (e.g. `/mock/`) or on a different port entirely. This gives a clear separation between the control plane and the data plane.

---

### 10. No delete endpoint

**Problem:** There is no way to remove a registered mock via the API or UI. Mocks accumulate indefinitely and can only be cleared by editing the JSON file directly or dropping the MongoDB collection.

**Production remedy:** Add `DELETE /virtual_apis?api=<path>&method=<METHOD>` and a corresponding UI button.
