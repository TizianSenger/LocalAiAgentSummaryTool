"""
Pydantic models for request/response validation across the API.
These are the shared data contracts between the frontend and backend.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FolderType(str, Enum):
    """The two types of study containers (only Lernfach is active in v1)."""
    LERNFACH = "Lernfach"
    PRAKTISCHE_AUFGABE = "Praktische Aufgabe"


class FolderCreate(BaseModel):
    """Payload sent by the frontend when creating a new folder."""
    name: str = Field(..., min_length=1, max_length=100)
    folder_type: FolderType = FolderType.LERNFACH


class FolderInfo(BaseModel):
    """Describes a single study folder returned by GET /folders."""
    name: str
    safe_name: str           # Filesystem-safe version of name (underscores, no special chars)
    folder_type: FolderType
    has_pdf: bool
    has_markdown: bool
    has_summary: bool
    created_at: str
    pdf_filename: Optional[str] = None


class FolderSettings(BaseModel):
    """
    Per-folder AI summarization settings stored in settings.json.
    All fields have sensible defaults so new folders work out of the box.
    """
    ollama_model: str = "llama3.1"
    system_prompt: str = ""
    summary_length: str = Field(
        default="medium",
        pattern="^(short|medium|long|comprehensive)$"
    )
    language: str = "de"
    include_images: bool = True
    include_tables: bool = True
    include_formulas: bool = True
    include_code: bool = True
    chunk_size: int = Field(default=3000, ge=500, le=10000)
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)
    use_vision: bool = False
    vision_model: str = "llama3.2-vision:11b"
