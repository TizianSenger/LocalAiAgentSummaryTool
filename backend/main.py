"""
FastAPI backend for StudyScript AI.

All heavy work (PDF conversion, Ollama inference) runs asynchronously so the
server stays responsive while a large document is being processed. Real-time
progress is pushed to the browser via a persistent WebSocket connection.

Endpoints at a glance:
  GET  /health                         → liveness probe
  GET  /folders                        → list all study folders
  POST /folders                        → create folder
  DEL  /folders/{name}                 → delete folder
  POST /folders/{name}/upload          → upload PDF
  POST /folders/{name}/convert         → PDF → Markdown (streams progress via WS)
  POST /folders/{name}/summarize       → Markdown → Summary (streams progress via WS)
  GET  /folders/{name}/settings        → load AI settings
  PUT  /folders/{name}/settings        → save AI settings
  GET  /folders/{name}/markdown        → read converted Markdown text
  GET  /folders/{name}/summary         → read summary Markdown text
  GET  /ollama/models                  → list locally installed Ollama models
  WS   /ws/{client_id}                 → progress push channel
"""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from dotenv import load_dotenv

# Load .env from project root (one level above this file) so ANTHROPIC_API_KEY
# is available for Claude vision calls without polluting the system environment.
load_dotenv(Path(__file__).parent.parent / ".env")
from fastapi import FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from folder_manager import FolderManager
from models import FolderCreate
from ollama_service import OllamaService
from pdf_converter import PDFConverter
from settings_manager import SettingsManager

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Data directory is one level above this file: project_root/data/
BASE_DATA_DIR = Path(__file__).parent.parent / "data"

folder_manager = FolderManager(BASE_DATA_DIR)
pdf_converter = PDFConverter()
ollama_service = OllamaService()
settings_manager = SettingsManager()

# Maps client_id → open WebSocket; used to push progress updates
_websockets: dict[str, WebSocket] = {}

# Cancel flags: set to True by POST /cancel; polled by running operations
_cancel_flags: dict[str, bool] = {}

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Ensure the data directory exists before handling any requests."""
    BASE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="StudyScript AI",
    version="1.0.0",
    lifespan=lifespan,
)

# Electron's renderer runs on file:// or localhost – allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# WebSocket – real-time progress updates
# ---------------------------------------------------------------------------


@app.websocket("/ws/{client_id}")
async def websocket_progress(websocket: WebSocket, client_id: str):
    """
    Persistent WebSocket used only for server→client progress messages.
    The client sends the same client_id as a query param to /convert and
    /summarize so the backend knows which socket to write to.
    """
    await websocket.accept()
    _websockets[client_id] = websocket
    try:
        while True:
            await websocket.receive_text()   # keep-alive; we only send, not receive
    except WebSocketDisconnect:
        _websockets.pop(client_id, None)


async def _push(client_id: Optional[str], percent: int, message: str):
    """Send a progress update to one specific WebSocket client (fire-and-forget)."""
    ws = _websockets.get(client_id) if client_id else None
    if ws:
        try:
            await ws.send_json({"type": "progress", "progress": percent, "message": message})
        except Exception:
            pass   # Client disconnected mid-operation – ignore silently


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    """Simple liveness probe – the Electron main process polls this on startup."""
    return {"status": "ok", "version": "1.0.0"}


@app.post("/cancel")
async def cancel_operation(client_id: Optional[str] = Query(default=None)):
    """Signal the running convert/summarize operation for this client to stop."""
    if client_id:
        _cancel_flags[client_id] = True
    return {"message": "Abbruch angefordert"}


# ---------------------------------------------------------------------------
# Folder management
# ---------------------------------------------------------------------------


@app.get("/folders")
async def list_folders():
    """Return all study folders found in the data directory."""
    return folder_manager.list_folders()


@app.post("/folders", status_code=201)
async def create_folder(body: FolderCreate):
    """Create a new study folder with the standard subdirectory structure."""
    return folder_manager.create_folder(body.name, body.folder_type)


@app.delete("/folders/{safe_name}")
async def delete_folder(safe_name: str):
    """Permanently delete a study folder and all its contents."""
    return folder_manager.delete_folder(safe_name)


# ---------------------------------------------------------------------------
# PDF upload
# ---------------------------------------------------------------------------


@app.post("/folders/{safe_name}/upload")
async def upload_pdf(safe_name: str, file: UploadFile = File(...)):
    """
    Upload a PDF into {safe_name}/original/.
    Only one PDF per folder is kept; any existing PDF is replaced.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Nur PDF-Dateien werden akzeptiert.")
    return await folder_manager.upload_pdf(safe_name, file)


# ---------------------------------------------------------------------------
# PDF conversion
# ---------------------------------------------------------------------------


@app.post("/folders/{safe_name}/convert")
async def convert_pdf(
    safe_name: str,
    client_id: Optional[str] = Query(default=None),
):
    """
    Convert the uploaded PDF to Markdown with image extraction.
    Progress updates are streamed to the WebSocket identified by client_id.
    """
    _cancel_flags.pop(client_id, None)  # clear any stale flag

    async def cb(p: int, m: str):
        await _push(client_id, p, m)

    def cancel_check() -> bool:
        return _cancel_flags.pop(client_id, False)

    return await pdf_converter.convert(safe_name, BASE_DATA_DIR, cb, cancel_check)


# ---------------------------------------------------------------------------
# AI summarization
# ---------------------------------------------------------------------------


@app.post("/folders/{safe_name}/summarize")
async def summarize(
    safe_name: str,
    client_id: Optional[str] = Query(default=None),
):
    """
    Summarize the converted Markdown using Ollama.
    Model, system prompt, and length are read from the folder's settings.json.
    """
    _cancel_flags.pop(client_id, None)  # clear any stale flag
    loaded_settings = settings_manager.load_settings(safe_name, BASE_DATA_DIR)

    async def cb(p: int, m: str):
        await _push(client_id, p, m)

    def cancel_check() -> bool:
        return _cancel_flags.pop(client_id, False)

    return await ollama_service.summarize(safe_name, BASE_DATA_DIR, loaded_settings, cb, cancel_check)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@app.get("/folders/{safe_name}/settings")
async def get_settings(safe_name: str):
    """Return the current AI settings for a folder."""
    return settings_manager.load_settings(safe_name, BASE_DATA_DIR)


@app.put("/folders/{safe_name}/settings")
async def update_settings(safe_name: str, body: dict):
    """Persist updated AI settings for a folder."""
    return settings_manager.save_settings(safe_name, BASE_DATA_DIR, body)


# ---------------------------------------------------------------------------
# Content retrieval (Markdown text for in-app preview)
# ---------------------------------------------------------------------------


@app.get("/folders/{safe_name}/markdown")
async def get_markdown(safe_name: str):
    """Return the converted Markdown file as plain text for preview."""
    converted_dir = BASE_DATA_DIR / safe_name / "converted"
    md_files = list(converted_dir.glob("*.md"))
    if not md_files:
        raise HTTPException(status_code=404, detail="Noch kein konvertiertes Markdown vorhanden.")
    return {"content": md_files[0].read_text(encoding="utf-8")}


@app.get("/folders/{safe_name}/summary")
async def get_summary(safe_name: str):
    """Return the summary Markdown file as plain text for preview."""
    summary_path = BASE_DATA_DIR / safe_name / "summary" / "summary.md"
    if not summary_path.exists():
        raise HTTPException(status_code=404, detail="Noch keine Zusammenfassung vorhanden.")
    return {"content": summary_path.read_text(encoding="utf-8")}


# ---------------------------------------------------------------------------
# Ollama model list
# ---------------------------------------------------------------------------


@app.get("/ollama/models")
async def get_ollama_models():
    """List all models currently installed in the local Ollama instance."""
    return await ollama_service.list_models()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )
