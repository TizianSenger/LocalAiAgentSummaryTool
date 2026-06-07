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

Progress reporting: a parallel async ticker advances the progress bar through
realistic stage labels while the blocking conversion runs. The interval between
stages is estimated from the page count (~0.35 s per page per stage).
"""
import asyncio
import shutil
from pathlib import Path
from typing import Awaitable, Callable, Optional

# Ordered conversion stages shown in the progress bar.
# Percentages run from 10 % (after model load) to 93 % (before "done").
_STAGES: list[tuple[int, str]] = [
    (10, "Analysiere PDF-Struktur und Seiten-Layout…"),
    (20, "Erkenne Überschriften und Textblöcke…"),
    (32, "Extrahiere und interpretiere Fließtext…"),
    (44, "Verarbeite mathematische Formeln (LaTeX)…"),
    (56, "Erkenne Tabellen und wandle sie um…"),
    (66, "Identifiziere Code-Blöcke und Syntax…"),
    (76, "Extrahiere eingebettete Bilder…"),
    (86, "Generiere Markdown-Dokument…"),
    (93, "Speichere Ausgabe auf Festplatte…"),
]


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
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> dict:
        """
        Convert the PDF found in {base_dir}/{safe_name}/original/ to Markdown.

        Output is written to:
            converted/{stem}.md        ← full Markdown document
            converted/images/*.png     ← extracted images referenced in the Markdown

        A parallel ticker task advances the progress bar through realistic stage
        labels while the blocking conversion runs in a thread-pool executor.

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

        # Read page count up front so the ticker can estimate timing
        page_count = self._get_page_count(pdf_path)

        await self._emit(progress, 2, f"PDF geladen – {page_count} Seiten erkannt")

        if cancel_check and cancel_check():
            await self._emit(progress, 0, "⚠ Vorgang durch Benutzer abgebrochen.")
            raise RuntimeError("Abgebrochen")

        await self._emit(progress, 5, f"Lade KI-Modelle in GPU-Speicher… (marker-pdf + surya-ocr)")

        # Start the progress ticker in the background, then run the blocking conversion
        ticker = asyncio.create_task(self._progress_ticker(progress, page_count))

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._convert_blocking,
                pdf_path,
                md_path,
                images_dir,
            )
        finally:
            # Always stop the ticker, whether conversion succeeded or raised
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass

        await self._emit(
            progress, 100,
            f"Fertig! {result['pages_count']} Seiten, {result['images_count']} Bilder extrahiert."
        )

        return {
            "message": "PDF erfolgreich in Markdown konvertiert.",
            "markdown_file": md_path.name,
            "images_count": result["images_count"],
            "pages_count": result["pages_count"],
        }

    # ------------------------------------------------------------------
    # Progress ticker (runs in async context while conversion blocks)
    # ------------------------------------------------------------------

    async def _progress_ticker(
        self,
        progress: Optional[Callable[[int, str], Awaitable[None]]],
        page_count: int,
    ):
        """
        Advance the progress bar through predefined stages while the blocking
        conversion runs in a thread-pool executor.

        The interval between stages is estimated from the page count so the bar
        moves at a natural pace regardless of document length:
          50 pages  → ~3 s per stage
          172 pages → ~10 s per stage
          500 pages → ~30 s per stage
        """
        # Rough estimate: marker-pdf needs ~0.25 s per page per stage on GPU
        seconds_per_stage = max(3.0, page_count * 0.25)

        for percent, message in _STAGES:
            await self._emit(progress, percent, message)
            try:
                await asyncio.sleep(seconds_per_stage)
            except asyncio.CancelledError:
                # Conversion finished – stop updating silently
                return

    # ------------------------------------------------------------------
    # Blocking conversion (runs in thread pool)
    # ------------------------------------------------------------------

    def _convert_blocking(
        self,
        pdf_path: Path,
        md_path: Path,
        images_dir: Path,
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
        pages_count = self._get_page_count(pdf_path)

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
            # marker writes bare filenames; prefix so they resolve from the .md location
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
        import pymupdf4llm

        pages_count = self._get_page_count(pdf_path)

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
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_page_count(pdf_path: Path) -> int:
        """Return the number of pages in a PDF using PyMuPDF."""
        import fitz
        with fitz.open(str(pdf_path)) as doc:
            return len(doc)

    @staticmethod
    async def _emit(
        callback: Optional[Callable[[int, str], Awaitable[None]]],
        percent: int,
        message: str,
    ):
        """Fire the progress callback if one was supplied."""
        if callback:
            await callback(percent, message)
