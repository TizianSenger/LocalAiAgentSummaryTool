"""
Manages the lifecycle of study folders: create, list, delete, and PDF upload.

Every study folder has the following on-disk layout:

    data/
    └── {safe_name}/
        ├── meta.json        ← name, type, creation timestamp
        ├── settings.json    ← AI summarization settings (see settings_manager.py)
        ├── original/        ← the uploaded PDF lives here
        ├── converted/       ← marker-pdf output: .md file + images/
        │   └── images/
        └── summary/         ← Ollama-generated summary .md + images/
            └── images/
"""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile

from models import FolderInfo, FolderType
from settings_manager import SettingsManager


class FolderManager:
    """CRUD operations for study folders on the local filesystem."""

    def __init__(self, base_dir: Path):
        """
        Args:
            base_dir: Root directory where all study folders are stored.
                      Created automatically if it does not exist.
        """
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_folders(self) -> list[FolderInfo]:
        """
        Scan base_dir and return metadata for every valid study folder.
        A valid folder contains a meta.json file written by create_folder().

        Returns:
            List of FolderInfo objects sorted newest-first.
        """
        folders: list[FolderInfo] = []

        for folder_path in self.base_dir.iterdir():
            if not folder_path.is_dir():
                continue

            meta_file = folder_path / "meta.json"
            if not meta_file.exists():
                continue

            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            original_dir = folder_path / "original"
            converted_dir = folder_path / "converted"
            summary_dir = folder_path / "summary"
            vault_dir = folder_path / "vault"

            pdf_files = list(original_dir.glob("*.pdf")) if original_dir.exists() else []
            md_files = list(converted_dir.glob("*.md")) if converted_dir.exists() else []
            summary_files = list(summary_dir.glob("*.md")) if summary_dir.exists() else []
            vault_files = list(vault_dir.glob("*.md")) if vault_dir.exists() else []

            folders.append(FolderInfo(
                name=meta["name"],
                safe_name=meta["safe_name"],
                folder_type=FolderType(meta["folder_type"]),
                has_pdf=bool(pdf_files),
                has_markdown=bool(md_files),
                has_summary=bool(summary_files),
                has_vault=bool(vault_files),
                created_at=meta["created_at"],
                pdf_filename=pdf_files[0].name if pdf_files else None,
            ))

        return sorted(folders, key=lambda f: f.created_at, reverse=True)

    def create_folder(self, name: str, folder_type: FolderType) -> FolderInfo:
        """
        Create a new study folder with the standard subdirectory layout.

        Args:
            name:        Human-readable display name (may contain spaces/umlauts)
            folder_type: Lernfach or Praktische Aufgabe

        Returns:
            FolderInfo describing the freshly created folder

        Raises:
            HTTPException 409 if a folder with the same safe name already exists
        """
        safe_name = self._to_safe_name(name)
        folder_path = self.base_dir / safe_name

        if folder_path.exists():
            raise HTTPException(
                status_code=409,
                detail=f"Ein Ordner mit dem Namen '{name}' existiert bereits."
            )

        # Create the complete directory tree in one call each
        (folder_path / "original").mkdir(parents=True)
        (folder_path / "converted" / "images").mkdir(parents=True)
        (folder_path / "summary" / "images").mkdir(parents=True)

        created_at = datetime.now().isoformat()

        # Persist metadata so list_folders() can identify this directory
        meta = {
            "name": name,
            "safe_name": safe_name,
            "folder_type": folder_type.value,
            "created_at": created_at,
        }
        (folder_path / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # Write default AI settings immediately so the settings panel is never empty
        SettingsManager().save_settings(safe_name, self.base_dir, SettingsManager.default_settings())

        return FolderInfo(
            name=name,
            safe_name=safe_name,
            folder_type=folder_type,
            has_pdf=False,
            has_markdown=False,
            has_summary=False,
            created_at=created_at,
        )

    def delete_folder(self, safe_name: str) -> dict:
        """
        Permanently delete a folder and all its contents.

        Args:
            safe_name: Filesystem-safe folder name (as returned in FolderInfo.safe_name)

        Raises:
            HTTPException 404 if the folder does not exist
        """
        folder_path = self.base_dir / safe_name

        if not folder_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Ordner '{safe_name}' wurde nicht gefunden."
            )

        shutil.rmtree(folder_path)
        return {"message": f"Ordner '{safe_name}' wurde erfolgreich gelöscht."}

    async def upload_pdf(self, safe_name: str, file: UploadFile) -> dict:
        """
        Save an uploaded PDF to {folder}/original/, replacing any existing PDF.
        One PDF per folder is enforced to keep the workflow simple.

        Args:
            safe_name: Filesystem-safe folder name
            file:      The multipart-uploaded PDF file

        Returns:
            Dict with the saved filename and path
        """
        folder_path = self.base_dir / safe_name
        if not folder_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Ordner '{safe_name}' wurde nicht gefunden."
            )

        original_dir = folder_path / "original"

        # Remove any previously uploaded PDF (one-PDF-per-folder rule)
        for existing in original_dir.glob("*.pdf"):
            existing.unlink()

        target = original_dir / (file.filename or "document.pdf")

        async with aiofiles.open(target, "wb") as out:
            content = await file.read()
            await out.write(content)

        return {
            "message": "PDF erfolgreich hochgeladen.",
            "filename": target.name,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_safe_name(name: str) -> str:
        """
        Convert a display name to a valid, cross-platform filesystem name.
        Spaces become underscores; everything that isn't alphanumeric,
        an underscore, or a hyphen is removed.

        Examples:
            "Mathematik 2"   → "Mathematik_2"
            "C++ Grundlagen" → "C_Grundlagen"
        """
        safe = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE)
        safe = re.sub(r"\s+", "_", safe)
        safe = safe.strip("_")
        return safe or "ordner"
