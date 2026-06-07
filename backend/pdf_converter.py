"""
Converts PDF files to Markdown using marker-pdf (primary) or pymupdf4llm (fallback).

marker-pdf uses deep-learning models that run on GPU (CUDA) and accurately handle:
  - Mathematical formulas → LaTeX ($...$, $$...$$)
  - Tables               → Markdown table syntax
  - Code blocks          → fenced code blocks with language hints
  - Images               → extracted as PNG files, referenced in the Markdown
  - Mixed-language text  → language detection per block

Because model loading and PDF processing are CPU/GPU-bound and can take minutes
for large documents, all blocking work runs in a thread-pool executor so the
FastAPI event loop stays responsive during conversion.
"""
import asyncio
import shutil
from pathlib import Path
from typing import Awaitable, Callable, Optional


class PDFConverter:
    """Converts a PDF in a study folder to Markdown + extracted images."""

    # ------------------------------------------------------------------
    # Public async API (called from FastAPI route handlers)
    # ------------------------------------------------------------------

    async def convert(
        self,
        safe_name: str,
        base_dir: Path,
        progress: Optional[Callable[[int, str], Awaitable[None]]] = None,
    ) -> dict:
        """
        Convert the PDF found in {base_dir}/{safe_name}/original/ to Markdown.

        Output is written to:
            converted/{stem}.md        ← full Markdown document
            converted/images/*.png     ← extracted images referenced in the Markdown

        Args:
            safe_name:  Filesystem-safe folder name
            base_dir:   Root data directory
            progress:   Async callback(percent, message) for real-time UI updates

        Returns:
            Dict summarising the conversion result
        """
        folder_path = base_dir / safe_name
        original_dir = folder_path / "original"
        converted_dir = folder_path / "converted"
        images_dir = converted_dir / "images"

        pdfs = list(original_dir.glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError("Kein PDF in diesem Ordner gefunden.")

        pdf_path = pdfs[0]
        md_path = converted_dir / (pdf_path.stem + ".md")

        # Clean previous conversion output before starting
        for old_md in converted_dir.glob("*.md"):
            old_md.unlink()
        if images_dir.exists():
            shutil.rmtree(images_dir)
        images_dir.mkdir(parents=True)

        await self._emit(progress, 5, "Lade KI-Modelle in den GPU-Speicher…")

        # Run the blocking conversion off the event loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            self._convert_blocking,
            pdf_path,
            md_path,
            images_dir,
            progress,   # passed for synchronous logging only; async calls happen above/below
        )

        await self._emit(progress, 100, "Konvertierung abgeschlossen!")

        return {
            "message": "PDF erfolgreich in Markdown konvertiert.",
            "markdown_file": md_path.name,
            "images_count": result["images_count"],
            "pages_count": result["pages_count"],
        }

    # ------------------------------------------------------------------
    # Blocking conversion (runs in thread pool)
    # ------------------------------------------------------------------

    def _convert_blocking(
        self,
        pdf_path: Path,
        md_path: Path,
        images_dir: Path,
        progress,   # not awaitable here – only used for sync logging
    ) -> dict:
        """
        Attempt conversion with marker-pdf; fall back to pymupdf4llm if it fails.
        Both approaches are tried so the app degrades gracefully when marker's
        optional heavy dependencies (torch, surya, etc.) are not installed.
        """
        try:
            return self._marker_convert(pdf_path, md_path, images_dir)
        except Exception as marker_err:
            print(f"[pdf_converter] marker-pdf fehlgeschlagen ({marker_err}), "
                  "verwende pymupdf4llm als Fallback.")
            return self._pymupdf_convert(pdf_path, md_path, images_dir)

    # ------------------------------------------------------------------
    # Strategy 1: marker-pdf (preferred – GPU-accelerated, best quality)
    # ------------------------------------------------------------------

    def _marker_convert(self, pdf_path: Path, md_path: Path, images_dir: Path) -> dict:
        """
        Use marker-pdf to convert the PDF.

        Supports two API versions to handle different installed releases:
          v0.x  →  convert_single_pdf() from marker.convert
          v1.x  →  PdfConverter class from marker.converters.pdf

        The resulting Markdown and images are written to disk.
        """
        import fitz  # PyMuPDF – used only for page count here

        with fitz.open(str(pdf_path)) as doc:
            pages_count = len(doc)

        try:
            # --- marker v1.x API ---
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
            from marker.output import text_from_rendered

            converter = PdfConverter(artifact_dict=create_model_dict())
            rendered = converter(str(pdf_path))
            full_text, _, images = text_from_rendered(rendered)

        except (ImportError, AttributeError):
            # --- marker v0.x API ---
            from marker.convert import convert_single_pdf
            from marker.models import load_all_models

            model_list = load_all_models()
            full_text, images, _ = convert_single_pdf(
                str(pdf_path),
                model_list,
                langs=["German", "English"],
            )

        # Save extracted images and fix their relative paths in the Markdown
        for img_name, img_obj in images.items():
            img_obj.save(str(images_dir / img_name))
            # marker writes bare filenames; prefix them so they resolve from the .md location
            full_text = full_text.replace(f"({img_name})", f"(images/{img_name})")

        md_path.write_text(full_text, encoding="utf-8")

        return {"images_count": len(images), "pages_count": pages_count}

    # ------------------------------------------------------------------
    # Strategy 2: pymupdf4llm (fallback – no GPU needed, lighter install)
    # ------------------------------------------------------------------

    def _pymupdf_convert(self, pdf_path: Path, md_path: Path, images_dir: Path) -> dict:
        """
        Use pymupdf4llm as a lightweight fallback.
        Quality is good for text, tables, and images but less accurate for
        complex math formulas (they become plain text instead of LaTeX).
        """
        import fitz
        import pymupdf4llm

        with fitz.open(str(pdf_path)) as doc:
            pages_count = len(doc)

        md_text = pymupdf4llm.to_markdown(
            str(pdf_path),
            write_images=True,
            image_path=str(images_dir),
            image_format="png",
        )

        images_count = len(list(images_dir.glob("*.png")))
        md_path.write_text(md_text, encoding="utf-8")

        return {"images_count": images_count, "pages_count": pages_count}

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    async def _emit(
        callback: Optional[Callable[[int, str], Awaitable[None]]],
        percent: int,
        message: str,
    ):
        """Fire the progress callback if one was supplied."""
        if callback:
            await callback(percent, message)
