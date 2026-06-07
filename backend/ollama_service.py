"""
Summarizes Markdown documents using a locally running Ollama model.

Strategy: map-reduce over chunks
---------------------------------
A 400-1500 page script converted to Markdown can be hundreds of thousands of
characters – far more than any model's context window. We therefore use a
map-reduce approach:

  1. SPLIT  – divide the Markdown into chunks at natural heading boundaries
  2. MAP    – ask Ollama to summarize each chunk individually
  3. REDUCE – combine all chunk summaries into one final, coherent document

Images referenced in the original Markdown are copied to the summary folder
so the summary remains a self-contained document with visuals.
"""
import asyncio
import base64
import re
import shutil
from pathlib import Path
from typing import Awaitable, Callable, Optional

def _extract_content(response) -> str:
    """
    Extract the text content from an ollama.chat() response.

    Handles both API versions:
      - v0.3 and older: response is a dict  → response["message"]["content"]
      - v0.4+ (current): response is a ChatResponse object
                         → response.message.content
    """
    if hasattr(response, "message"):
        # v0.4+ ChatResponse object
        return response.message.content
    # v0.3 plain dict
    return response["message"]["content"]


# Human-readable instruction appended to each chunk request to control length
_LENGTH_INSTRUCTIONS: dict[str, str] = {
    "short":         "Erstelle eine sehr kompakte Zusammenfassung (~20% des Originals). "
                     "Nur die absolut wichtigsten Definitionen und Konzepte.",
    "medium":        "Erstelle eine ausgewogene Zusammenfassung (~40% des Originals). "
                     "Alle wichtigen Konzepte mit kurzen Erklärungen.",
    "long":          "Erstelle eine detaillierte Zusammenfassung (~60% des Originals). "
                     "Umfassende Abdeckung aller Themen mit Beispielen.",
    "comprehensive": "Erstelle eine vollständige Zusammenfassung (~80% des Originals). "
                     "Behalte nahezu alle Details, Beweise und Beispiele bei.",
}


class OllamaService:
    """Interfaces with a locally running Ollama instance for AI summarization."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_models(self) -> list[dict]:
        """
        Return the models currently installed in Ollama.
        If Ollama is not reachable, returns an empty list (no crash).

        Handles both ollama library API versions:
          - v0.3 and older: response is a dict with response["models"] = list of dicts,
            each dict has key "name"
          - v0.4+ (current):  response is a ListResponse object with .models attribute,
            each model is a Model object with .model (name) and .size attributes
        """
        try:
            import ollama
            response = ollama.list()

            # v0.4+: ListResponse object with .models list of Model objects
            if hasattr(response, "models"):
                raw_models = response.models
            else:
                # v0.3: plain dict
                raw_models = response.get("models", [])

            result = []
            for m in raw_models:
                if hasattr(m, "model"):
                    # v0.4+ Model object: name is .model, size is .size
                    name = m.model
                    size = getattr(m, "size", 0) or 0
                else:
                    # v0.3 dict: name is "name", size is "size"
                    name = m.get("name") or m.get("model", "unknown")
                    size = m.get("size", 0) or 0

                result.append({
                    "name": name,
                    "size_gb": round(size / 1e9, 1),
                })

            return result

        except Exception as exc:
            print(f"[ollama_service] Ollama nicht erreichbar: {exc}")
            return []

    async def summarize(
        self,
        safe_name: str,
        base_dir: Path,
        settings: dict,
        progress: Optional[Callable[[int, str], Awaitable[None]]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> dict:
        """
        Summarize the converted Markdown and save the result to summary/summary.md.

        Args:
            safe_name:  Filesystem-safe folder name
            base_dir:   Root data directory
            settings:   AI settings dict (model, prompt, length, etc.)
            progress:   Async callback(percent, message) for real-time UI updates

        Returns:
            Dict with summary stats
        """
        folder_path = base_dir / safe_name
        converted_dir = folder_path / "converted"
        summary_dir = folder_path / "summary"
        summary_images_dir = summary_dir / "images"

        md_files = list(converted_dir.glob("*.md"))
        if not md_files:
            raise FileNotFoundError(
                "Kein konvertiertes Markdown gefunden. Bitte zuerst das PDF konvertieren."
            )

        md_content = md_files[0].read_text(encoding="utf-8")
        document_title = md_files[0].stem

        await self._emit(progress, 5, "Teile Dokument in Abschnitte auf…")

        chunks = self._split_into_chunks(md_content, settings.get("chunk_size", 3000))
        total = len(chunks)

        provider_label = "Claude API" if settings.get("ai_provider") == "claude" else f"Ollama ({settings.get('ollama_model', 'qwen2.5:14b')})"
        await self._emit(progress, 10, f"Dokument hat {total} Abschnitte – starte KI-Verarbeitung via {provider_label}…")

        # Summarize each chunk (blocking Ollama calls run off the event loop)
        loop = asyncio.get_event_loop()
        chunk_summaries: list[str] = []

        use_vision        = settings.get("use_vision", False)
        vision_provider   = settings.get("vision_provider", "ollama")
        vision_model      = settings.get("vision_model", "llama3.2-vision:11b")
        claude_vis_model  = settings.get("claude_vision_model", "claude-haiku-4-5-20251001")
        active_vis_label  = f"Claude ({claude_vis_model})" if vision_provider == "claude" else vision_model

        for i, chunk in enumerate(chunks):
            if cancel_check and cancel_check():
                await self._emit(progress, 0, "⚠ Vorgang durch Benutzer abgebrochen.")
                raise RuntimeError("Abgebrochen")

            pct = 10 + int((i / total) * 75)
            char_count = len(chunk)
            preview = chunk.replace('\n', ' ').strip()[:90]
            await self._emit(progress, pct,
                f"Fasse Abschnitt {i + 1} von {total} zusammen… ({char_count} Zeichen)")
            await self._emit(progress, pct, f"↳ {preview}…")

            # Optional: replace image references with vision model descriptions
            if use_vision and re.search(r'!\[.*?\]\(images/', chunk):
                img_count = len(re.findall(r'!\[.*?\]\(images/', chunk))
                await self._emit(progress, pct,
                    f"↳ Analysiere {img_count} Bild(er) via {active_vis_label}…")
                chunk = await loop.run_in_executor(
                    None,
                    self._enrich_chunk_with_vision,
                    chunk,
                    converted_dir / "images",
                    vision_provider,
                    vision_model,
                    claude_vis_model,
                )

            summary = await loop.run_in_executor(
                None,
                self._summarize_chunk,
                chunk,
                settings,
                i + 1,
                total,
            )
            chunk_summaries.append(summary)

            resp_preview = summary.replace('\n', ' ').strip()[:90]
            await self._emit(progress, pct,
                f"✓ Chunk {i + 1} fertig – KI: {resp_preview}…")

        await self._emit(progress, 88, "Erstelle finales Dokument…")

        final_md = await loop.run_in_executor(
            None,
            self._merge_summaries,
            chunk_summaries,
            settings,
            document_title,
        )

        # Copy images referenced in the summary to the summary/images/ directory
        copied = self._copy_referenced_images(
            final_md,
            converted_dir / "images",
            summary_images_dir,
        )

        (summary_dir / "summary.md").write_text(final_md, encoding="utf-8")

        await self._emit(progress, 100, "Zusammenfassung abgeschlossen!")

        return {
            "message": "Zusammenfassung erfolgreich erstellt.",
            "summary_file": "summary.md",
            "chunks_processed": total,
            "images_copied": copied,
        }

    # ------------------------------------------------------------------
    # Text splitting
    # ------------------------------------------------------------------

    def _split_into_chunks(self, text: str, chunk_size: int) -> list[str]:
        """
        Split Markdown text into chunks of approximately chunk_size characters.

        Splitting respects structure:
          - Never cuts inside a fenced code block (``` ... ```)
          - Prefers to split at Markdown headings (# / ## / ###)
          - Falls back to splitting at blank lines if no heading is near
        """
        lines = text.split("\n")
        chunks: list[str] = []
        current: list[str] = []
        current_size = 0
        in_code_block = False

        for line in lines:
            if line.startswith("```"):
                in_code_block = not in_code_block

            current.append(line)
            current_size += len(line) + 1  # +1 for the newline

            is_heading = line.startswith("#") and not in_code_block
            over_limit = current_size >= chunk_size and not in_code_block

            if over_limit and (is_heading or current_size >= chunk_size * 1.5):
                chunks.append("\n".join(current))
                current = []
                current_size = 0

        if current:
            chunks.append("\n".join(current))

        return [c for c in chunks if c.strip()]

    # ------------------------------------------------------------------
    # Vision – image analysis (blocking – run in executor)
    # ------------------------------------------------------------------

    def _enrich_chunk_with_vision(
        self,
        chunk: str,
        images_dir: Path,
        provider: str,
        ollama_model: str,
        claude_model: str,
    ) -> str:
        """
        Find every image reference in a Markdown chunk, describe it with the
        chosen vision provider, and replace ![...](images/...) with a blockquote
        so the text summarizer understands the visual content.
        """
        def replace_image(match: re.Match) -> str:
            alt      = match.group(1)
            filename = match.group(2)
            image_path = images_dir / filename
            if provider == "claude":
                desc = self._describe_image_claude(image_path, claude_model, alt)
            else:
                desc = self._describe_image_ollama(image_path, ollama_model, alt)
            if desc:
                label = alt or filename
                return f'\n> 📷 **Abbildung – {label}:** {desc}\n'
            return match.group(0)

        return re.sub(r'!\[([^\]]*)\]\(images/([^)]+)\)', replace_image, chunk)

    @staticmethod
    def _describe_image_ollama(image_path: Path, vision_model: str, context: str = "") -> str:
        """Describe an image using a local Ollama vision model."""
        import ollama

        if not image_path.exists():
            return ""
        try:
            with open(image_path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode()

            ctx_hint = f' (Kontext: "{context}")' if context else ""
            prompt = (
                f"Beschreibe dieses Bild aus einem wissenschaftlichen Lernscript "
                f"präzise und knapp auf Deutsch{ctx_hint}. "
                "Was zeigt es? (z.B. Diagramm, Formel, Graph, Schaltkreis, Tabelle…) "
                "Antworte nur mit der Beschreibung, ohne Einleitung."
            )
            response = ollama.chat(
                model=vision_model,
                messages=[{"role": "user", "content": prompt, "images": [image_b64]}],
            )
            desc = _extract_content(response).strip()
            print(f"[vision/ollama] {image_path.name}: {desc[:80]}…")
            return desc
        except Exception as exc:
            print(f"[vision/ollama] Fehler bei {image_path.name}: {exc}")
            return ""

    @staticmethod
    def _describe_image_claude(image_path: Path, claude_model: str, context: str = "") -> str:
        """Describe an image using the Anthropic Claude API (requires ANTHROPIC_API_KEY in .env)."""
        import os
        import anthropic

        if not image_path.exists():
            return ""

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or api_key == "dein-api-key-hier":
            print("[vision/claude] ANTHROPIC_API_KEY nicht konfiguriert – überspringe Bild.")
            return ""

        try:
            with open(image_path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode()

            suffix = image_path.suffix.lower().lstrip(".")
            media_type = {"png": "image/png", "jpg": "image/jpeg",
                          "jpeg": "image/jpeg", "webp": "image/webp",
                          "gif": "image/gif"}.get(suffix, "image/png")

            ctx_hint = f' (Kontext: "{context}")' if context else ""
            prompt = (
                f"Beschreibe dieses Bild aus einem wissenschaftlichen Lernscript "
                f"präzise und knapp auf Deutsch{ctx_hint}. "
                "Was zeigt es? (z.B. Diagramm, Formel, Graph, Schaltkreis, Tabelle…) "
                "Antworte nur mit der Beschreibung, ohne Einleitung."
            )

            client = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model=claude_model,
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        }},
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            desc = message.content[0].text.strip()
            print(f"[vision/claude] {image_path.name}: {desc[:80]}…")
            return desc
        except Exception as exc:
            print(f"[vision/claude] Fehler bei {image_path.name}: {exc}")
            return ""

    # ------------------------------------------------------------------
    # Ollama calls (blocking – run in executor)
    # ------------------------------------------------------------------

    def _summarize_chunk(
        self,
        chunk: str,
        settings: dict,
        chunk_number: int,
        total_chunks: int,
    ) -> str:
        """Route chunk summarization to Ollama or Claude based on ai_provider setting."""
        if settings.get("ai_provider", "ollama") == "claude":
            return self._summarize_chunk_claude(chunk, settings, chunk_number, total_chunks)
        return self._summarize_chunk_ollama(chunk, settings, chunk_number, total_chunks)

    @staticmethod
    def _summarize_chunk_ollama(
        chunk: str, settings: dict, chunk_number: int, total_chunks: int
    ) -> str:
        """Summarize one chunk via local Ollama model."""
        import ollama

        length_hint = _LENGTH_INSTRUCTIONS.get(
            settings.get("summary_length", "medium"), _LENGTH_INSTRUCTIONS["medium"]
        )
        user_msg = (
            f"Hier ist Abschnitt {chunk_number} von {total_chunks} eines Lernscripts:\n\n"
            f"---\n{chunk}\n---\n\n{length_hint}\n\n"
            "Antworte ausschließlich mit dem zusammengefassten Markdown. "
            "Keine Einleitung, keine Erklärung – direkt das Markdown."
        )
        response = ollama.chat(
            model=settings.get("ollama_model", "qwen2.5:14b"),
            messages=[
                {"role": "system", "content": settings.get("system_prompt", "")},
                {"role": "user",   "content": user_msg},
            ],
            options={"temperature": settings.get("temperature", 0.3), "num_ctx": 8192},
        )
        return _extract_content(response)

    @staticmethod
    def _summarize_chunk_claude(
        chunk: str, settings: dict, chunk_number: int, total_chunks: int
    ) -> str:
        """Summarize one chunk via Anthropic Claude API."""
        import os
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or api_key == "dein-api-key-hier":
            raise RuntimeError("ANTHROPIC_API_KEY nicht in .env konfiguriert.")

        length_hint = _LENGTH_INSTRUCTIONS.get(
            settings.get("summary_length", "medium"), _LENGTH_INSTRUCTIONS["medium"]
        )
        user_msg = (
            f"Hier ist Abschnitt {chunk_number} von {total_chunks} eines Lernscripts:\n\n"
            f"---\n{chunk}\n---\n\n{length_hint}\n\n"
            "Antworte ausschließlich mit dem zusammengefassten Markdown. "
            "Keine Einleitung, keine Erklärung – direkt das Markdown."
        )
        try:
            client = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model=settings.get("claude_model", "claude-haiku-4-5-20251001"),
                max_tokens=4096,
                temperature=settings.get("temperature", 0.3),
                system=settings.get("system_prompt", ""),
                messages=[{"role": "user", "content": user_msg}],
            )
            return message.content[0].text
        except anthropic.AuthenticationError:
            raise RuntimeError("Claude API: Ungültiger API Key. Bitte .env prüfen.")
        except anthropic.BadRequestError as e:
            if "credit" in str(e).lower() or "balance" in str(e).lower():
                raise RuntimeError(
                    "Claude API: Kein Guthaben. Bitte unter console.anthropic.com aufladen."
                )
            raise RuntimeError(f"Claude API Fehler: {e}")
        except anthropic.RateLimitError:
            raise RuntimeError("Claude API: Rate Limit erreicht. Bitte kurz warten.")
        except Exception as e:
            raise RuntimeError(f"Claude API nicht erreichbar: {e}")

    def _merge_summaries(self, summaries: list[str], settings: dict, title: str) -> str:
        """Route final merge to Ollama or Claude based on ai_provider setting."""
        if settings.get("ai_provider", "ollama") == "claude":
            return self._merge_summaries_claude(summaries, settings, title)
        return self._merge_summaries_ollama(summaries, settings, title)

    @staticmethod
    def _merge_summaries_ollama(
        summaries: list[str],
        settings: dict,
        title: str,
    ) -> str:
        """
        Combine individual chunk summaries into one coherent Markdown document.

        For shorter combined texts (< 20 000 chars) a final Ollama pass removes
        duplicates and adds a table of contents. For very long combined texts the
        chunks are concatenated directly with a generated header.

        Args:
            summaries: List of per-chunk Markdown summaries
            settings:  AI settings
            title:     Original document filename stem (used as the H1 heading)

        Returns:
            Final Markdown document as a string
        """
        import ollama

        combined = "\n\n---\n\n".join(summaries)

        if len(combined) < 20_000:
            # Final unification pass to create a professional study document
            response = ollama.chat(
                model=settings.get("ollama_model", "llama3.1"),
                messages=[
                    {"role": "system", "content": settings.get("system_prompt", "")},
                    {
                        "role": "user",
                        "content": (
                            "Das sind die Teilzusammenfassungen eines Lernscripts. "
                            "Erstelle daraus ein einheitliches, gut strukturiertes Lerndokument.\n"
                            "- Füge ein Markdown-Inhaltsverzeichnis am Anfang ein\n"
                            "- Entferne Duplikate und verbinde verwandte Themen\n"
                            "- Behalte Formeln, Tabellen und Codeblöcke bei\n\n"
                            f"{combined}\n\n"
                            "Antworte nur mit dem finalen Markdown-Dokument."
                        ),
                    },
                ],
                options={
                    "temperature": settings.get("temperature", 0.3),
                    "num_ctx": 8192,
                },
            )
            body = _extract_content(response)
        else:
            # Document too large for a unification pass – concatenate directly
            body = combined

        return f"# Zusammenfassung: {title}\n\n{body}"

    @staticmethod
    def _merge_summaries_claude(summaries: list[str], settings: dict, title: str) -> str:
        """Merge chunk summaries into one document using Claude API."""
        import os
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or api_key == "dein-api-key-hier":
            raise RuntimeError("ANTHROPIC_API_KEY nicht in .env konfiguriert.")

        combined = "\n\n---\n\n".join(summaries)

        if len(combined) < 20_000:
            merge_prompt = (
                "Das sind die Teilzusammenfassungen eines Lernscripts. "
                "Erstelle daraus ein einheitliches, gut strukturiertes Lerndokument.\n"
                "- Füge ein Markdown-Inhaltsverzeichnis am Anfang ein\n"
                "- Entferne Duplikate und verbinde verwandte Themen\n"
                "- Behalte Formeln, Tabellen und Codeblöcke bei\n\n"
                f"{combined}\n\n"
                "Antworte nur mit dem finalen Markdown-Dokument."
            )
            try:
                client = anthropic.Anthropic(api_key=api_key)
                message = client.messages.create(
                    model=settings.get("claude_model", "claude-haiku-4-5-20251001"),
                    max_tokens=8192,
                    temperature=settings.get("temperature", 0.3),
                    system=settings.get("system_prompt", ""),
                    messages=[{"role": "user", "content": merge_prompt}],
                )
                body = message.content[0].text
            except anthropic.AuthenticationError:
                raise RuntimeError("Claude API: Ungültiger API Key. Bitte .env prüfen.")
            except anthropic.BadRequestError as e:
                if "credit" in str(e).lower() or "balance" in str(e).lower():
                    raise RuntimeError(
                        "Claude API: Kein Guthaben. Bitte unter console.anthropic.com aufladen."
                    )
                raise RuntimeError(f"Claude API Fehler: {e}")
            except anthropic.RateLimitError:
                raise RuntimeError("Claude API: Rate Limit erreicht. Bitte kurz warten.")
            except Exception as e:
                raise RuntimeError(f"Claude API nicht erreichbar: {e}")
        else:
            body = combined

        return f"# Zusammenfassung: {title}\n\n{body}"

    # ------------------------------------------------------------------
    # Image handling
    # ------------------------------------------------------------------

    @staticmethod
    def _copy_referenced_images(md_text: str, src_dir: Path, dst_dir: Path) -> int:
        """
        Copy every image referenced in the Markdown from src_dir to dst_dir.
        Only copies files that actually exist (references to missing images are skipped).

        Returns:
            Number of images successfully copied
        """
        # Match Markdown image syntax: ![alt text](images/filename.ext)
        refs = re.findall(r"!\[.*?\]\(images/(.*?)\)", md_text)
        copied = 0

        for filename in refs:
            src = src_dir / filename
            if src.exists():
                shutil.copy2(src, dst_dir / filename)
                copied += 1

        return copied

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    async def _emit(
        callback: Optional[Callable[[int, str], Awaitable[None]]],
        percent: int,
        message: str,
    ):
        """Fire the progress callback if one was provided."""
        if callback:
            await callback(percent, message)
