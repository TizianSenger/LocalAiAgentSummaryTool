"""
Summarizes Markdown documents using a locally running Ollama model.

Strategy: per-chapter summarization (zero information loss)
------------------------------------------------------------
A 400-1500 page script converted to Markdown can be hundreds of thousands of
characters – far more than any model's context window. We therefore use a
chapter-based approach:

  1. DETECT  – find primary heading level (# or ##) and split into chapters
  2. SPLIT   – divide each chapter into chunks at natural heading boundaries
  3. VISION  – one consolidated vision pass over ALL chunks (avoids model swapping)
  4. MAP     – summarize each chunk individually (text model)
  5. MERGE   – combine a chapter's chunk summaries into one chapter summary
  6. CONCAT  – assemble final document by concatenating all chapter summaries
               (NO final AI merge = guaranteed zero information loss)

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

        await self._emit(progress, 5, "Erkenne Kapitelstruktur…")

        chunk_size = settings.get("chunk_size", 3000)
        chapters = self._split_into_chapters(md_content)

        # Build per-chapter chunk lists; keep a flat list for the vision pass
        chapter_chunks: list[tuple[str, list[str]]] = [
            (title, self._split_into_chunks(content, chunk_size))
            for title, content in chapters
        ]
        all_chunks_flat = [c for _, chs in chapter_chunks for c in chs]

        total_chapters = len(chapter_chunks)
        total_chunks   = len(all_chunks_flat)

        provider_label = (
            "Claude API" if settings.get("ai_provider") == "claude"
            else f"Ollama ({settings.get('ollama_model', 'qwen2.5:14b')})"
        )
        await self._emit(progress, 10,
            f"{total_chapters} Kapitel erkannt, {total_chunks} Abschnitte gesamt – "
            f"starte KI-Verarbeitung via {provider_label}…")

        loop = asyncio.get_running_loop()

        use_vision       = settings.get("use_vision", False)
        vision_provider  = settings.get("vision_provider", "ollama")
        vision_model     = settings.get("vision_model", "llama3.2-vision:11b")
        claude_vis_model = settings.get("claude_vision_model", "claude-haiku-4-5-20251001")
        active_vis_label = (
            f"Claude ({claude_vis_model})" if vision_provider == "claude" else vision_model
        )

        # --- Pass 1: Vision enrichment over ALL chunks in one sweep ---
        enriched_flat = list(all_chunks_flat)
        if use_vision:
            image_jobs: list[tuple[int, str, str]] = []
            for ci, chunk in enumerate(all_chunks_flat):
                for m in re.finditer(r'!\[([^\]]*)\]\(images/([^)]+)\)', chunk):
                    image_jobs.append((ci, m.group(1), m.group(2)))

            if image_jobs:
                total_images  = len(image_jobs)
                unique_chunks = len({j[0] for j in image_jobs})
                await self._emit(progress, 10,
                    f"Bildanalyse: {total_images} Bild(er) in {unique_chunks} Abschnitt(en) "
                    f"via {active_vis_label}…")

                import time
                descriptions: dict[int, dict[str, str]] = {}

                for img_seq, (chunk_idx, alt, filename) in enumerate(image_jobs):
                    if cancel_check and cancel_check():
                        await self._emit(progress, 0, "⚠ Vorgang durch Benutzer abgebrochen.")
                        raise RuntimeError("Abgebrochen")

                    pct = 10 + int((img_seq / total_images) * 25)
                    await self._emit(progress, pct,
                        f"🖼 Bild {img_seq + 1}/{total_images}: {filename} "
                        f"(Abschnitt {chunk_idx + 1}/{total_chunks})…")

                    image_path = converted_dir / "images" / filename
                    t0 = time.monotonic()
                    if vision_provider == "claude":
                        desc = await loop.run_in_executor(
                            None, self._describe_image_claude, image_path, claude_vis_model, alt)
                    else:
                        desc = await loop.run_in_executor(
                            None, self._describe_image_ollama, image_path, vision_model, alt)
                    elapsed = time.monotonic() - t0

                    if chunk_idx not in descriptions:
                        descriptions[chunk_idx] = {}
                    descriptions[chunk_idx][filename] = desc

                    if desc:
                        await self._emit(progress, pct,
                            f"  ✓ {filename} ({elapsed:.1f}s): {desc[:80]}…")
                    else:
                        await self._emit(progress, pct,
                            f"  ⚠ {filename} ({elapsed:.1f}s): Keine Beschreibung – übersprungen")

                for ci, descs in descriptions.items():
                    def _apply(chunk: str, _descs: dict = descs) -> str:
                        def replace(m: re.Match) -> str:
                            d = _descs.get(m.group(2), "")
                            if d:
                                label = m.group(1) or m.group(2)
                                return f'\n> 📷 **Abbildung – {label}:** {d}\n'
                            return m.group(0)
                        return re.sub(r'!\[([^\]]*)\]\(images/([^)]+)\)', replace, chunk)
                    enriched_flat[ci] = _apply(all_chunks_flat[ci])

        # Distribute enriched chunks back to per-chapter lists
        chapter_enriched: list[tuple[str, list[str]]] = []
        offset = 0
        for title, orig_chunks in chapter_chunks:
            n = len(orig_chunks)
            chapter_enriched.append((title, enriched_flat[offset:offset + n]))
            offset += n

        # --- Pass 2: Text summarization + per-chapter merge ---
        await self._emit(progress, 35,
            f"Starte Textzusammenfassung: {total_chunks} Abschnitte in {total_chapters} Kapiteln…")

        chapter_summaries: list[tuple[str, str]] = []
        chunks_done = 0

        for ch_idx, (chapter_title, enriched_chunks) in enumerate(chapter_enriched):
            ch_num   = ch_idx + 1
            ch_total = len(enriched_chunks)
            chunk_sums: list[str] = []

            for i, chunk in enumerate(enriched_chunks):
                if cancel_check and cancel_check():
                    await self._emit(progress, 0, "⚠ Vorgang durch Benutzer abgebrochen.")
                    raise RuntimeError("Abgebrochen")

                chunks_done += 1
                pct = 35 + int((chunks_done / total_chunks) * 50)
                preview = chunk.replace('\n', ' ').strip()[:80]
                await self._emit(progress, pct,
                    f"[Kap. {ch_num}/{total_chapters}: {chapter_title[:40]}] "
                    f"Abschnitt {i + 1}/{ch_total} ({len(chunk)} Zeichen)")
                await self._emit(progress, pct, f"↳ {preview}…")

                s = await loop.run_in_executor(
                    None, self._summarize_chunk, chunk, settings, i + 1, ch_total)
                chunk_sums.append(s)
                await self._emit(progress, pct, f"  ✓ Abschnitt {i + 1}/{ch_total} fertig")

            # Merge this chapter's chunk summaries into one chapter section
            pct = 86 + int((ch_idx / total_chapters) * 12)
            await self._emit(progress, pct,
                f"Merge Kapitel {ch_num}/{total_chapters}: {chapter_title}…")
            chapter_sum = await self._merge_chapter_async(chunk_sums, chapter_title, settings)
            chapter_summaries.append((chapter_title, chapter_sum))
            await self._emit(progress, pct,
                f"  ✓ Kapitel {ch_num} fertig ({len(chapter_sum)} Zeichen)")

        await self._emit(progress, 98, "Baue finales Dokument zusammen…")

        final_md = self._build_final_document(chapter_summaries, document_title)
        final_md = self._fix_image_paths(final_md, converted_dir / "images")

        summary_images_dir.mkdir(parents=True, exist_ok=True)
        copied = self._copy_referenced_images(
            final_md, converted_dir / "images", summary_images_dir)

        (summary_dir / "summary.md").write_text(final_md, encoding="utf-8")

        await self._emit(progress, 100, "Zusammenfassung abgeschlossen!")

        return {
            "message": "Zusammenfassung erfolgreich erstellt.",
            "summary_file": "summary.md",
            "chapters_processed": total_chapters,
            "chunks_processed": total_chunks,
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

    @staticmethod
    def _split_into_chapters(text: str) -> list[tuple[str, str]]:
        """
        Split the document at its primary structural heading level.

        Tries # first (≥ 2 occurrences = chapter boundaries), then ##, then ###.
        Returns [(chapter_title, chapter_content), ...].
        If no suitable level is found the whole document is returned as one chapter.
        """
        lines = text.split('\n')

        def _count(lvl: int) -> int:
            prefix = '#' * lvl + ' '
            deeper = '#' * (lvl + 1)
            return sum(1 for l in lines if l.startswith(prefix) and not l.startswith(deeper))

        split_level = next((lvl for lvl in [1, 2, 3] if _count(lvl) >= 2), None)
        if split_level is None:
            return [("Inhalt", text)]

        prefix = '#' * split_level + ' '
        deeper = '#' * (split_level + 1)

        chapters: list[tuple[str, str]] = []
        cur_title = ""
        cur_lines: list[str] = []

        for line in lines:
            if line.startswith(prefix) and not line.startswith(deeper):
                if cur_title and cur_lines:
                    content = '\n'.join(cur_lines).strip()
                    if len(content) > 300:
                        chapters.append((cur_title, content))
                # Strip bold markers for a cleaner title
                cur_title = line[split_level + 1:].strip().strip('*').strip()
                cur_lines = [line]
            else:
                cur_lines.append(line)

        if cur_title and cur_lines:
            content = '\n'.join(cur_lines).strip()
            if len(content) > 300:
                chapters.append((cur_title, content))

        return chapters if len(chapters) >= 2 else [("Inhalt", text)]

    # ------------------------------------------------------------------
    # Vision – image analysis (blocking – run in executor)
    # ------------------------------------------------------------------

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
            "Bilder: Manche Bilder haben darunter eine KI-Beschreibung (> 📷 ...). "
            "Nutze diese Beschreibung NUR als Entscheidungshilfe: Ist das Bild ein wichtiges "
            "Diagramm, Modell oder Visualisierung? Dann behalte NUR die Bildreferenz "
            "`![alt](images/dateiname)` – ohne die Beschreibung. Ist es dekorativ (Logo, Icon, "
            "Hintergrund)? Dann lass Bild und Beschreibung komplett weg.\n\n"
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
            "Bilder: Manche Bilder haben darunter eine KI-Beschreibung (> 📷 ...). "
            "Nutze diese Beschreibung NUR als Entscheidungshilfe: Ist das Bild ein wichtiges "
            "Diagramm, Modell oder Visualisierung? Dann behalte NUR die Bildreferenz "
            "`![alt](images/dateiname)` – ohne die Beschreibung. Ist es dekorativ (Logo, Icon, "
            "Hintergrund)? Dann lass Bild und Beschreibung komplett weg.\n\n"
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

    async def _merge_chapter_async(
        self,
        chunk_summaries: list[str],
        chapter_title: str,
        settings: dict,
    ) -> str:
        """
        Merge a single chapter's chunk summaries into one coherent section.

        A chapter typically has 3-15 chunks so the combined input is always small
        enough for a single API call — no hierarchical reduction needed.
        The output token budget (4096) is per-chapter, so every chapter gets
        its full share regardless of document size.
        """
        if len(chunk_summaries) == 1:
            return chunk_summaries[0]

        _IMG_HINT = (
            "Behalte Bild-Referenzen als `![alt](images/dateiname)` – "
            "kopiere den Dateinamen exakt (inkl. führendem Unterstrich, "
            "z.B. `_page_15_Picture_2.jpeg`). Keine Bildbeschreibungen darunter."
        )

        combined = "\n\n---\n\n".join(chunk_summaries)
        prompt = (
            f'Das sind die Abschnittszusammenfassungen für das Kapitel "{chapter_title}".\n'
            "Erstelle daraus eine vollständige, gut strukturierte Kapitelzusammenfassung.\n"
            "- Alle wichtigen Konzepte, Definitionen, Formeln und Tabellen behalten\n"
            "- Logisch mit Überschriften strukturieren\n"
            "- Duplikate entfernen, verwandte Themen verbinden\n"
            f"- {_IMG_HINT}\n\n"
            f"{combined}\n\n"
            "Antworte nur mit dem Markdown."
        )

        provider = settings.get("ai_provider", "ollama")
        loop = asyncio.get_running_loop()

        if provider == "claude":
            import os as _os
            import anthropic as _ant
            api_key = _os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key or api_key == "dein-api-key-hier":
                raise RuntimeError("ANTHROPIC_API_KEY nicht in .env konfiguriert.")

            def _call() -> str:
                try:
                    c = _ant.Anthropic(api_key=api_key)
                    r = c.messages.create(
                        model=settings.get("claude_model", "claude-haiku-4-5-20251001"),
                        max_tokens=4096,
                        temperature=settings.get("temperature", 0.3),
                        system=settings.get("system_prompt", ""),
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return r.content[0].text
                except _ant.AuthenticationError:
                    raise RuntimeError("Claude API: Ungültiger API Key. Bitte .env prüfen.")
                except _ant.BadRequestError as e:
                    if "credit" in str(e).lower() or "balance" in str(e).lower():
                        raise RuntimeError(
                            "Claude API: Kein Guthaben. Bitte unter console.anthropic.com aufladen.")
                    raise RuntimeError(f"Claude API Fehler: {e}")
                except _ant.RateLimitError:
                    raise RuntimeError("Claude API: Rate Limit erreicht. Bitte kurz warten.")
                except Exception as e:
                    raise RuntimeError(f"Claude API nicht erreichbar: {e}")
        else:
            import ollama as _oll

            def _call() -> str:
                r = _oll.chat(
                    model=settings.get("ollama_model", "qwen2.5:14b"),
                    messages=[
                        {"role": "system", "content": settings.get("system_prompt", "")},
                        {"role": "user",   "content": prompt},
                    ],
                    options={
                        "temperature": settings.get("temperature", 0.3),
                        "num_ctx": 32768,
                        "num_predict": 4096,
                    },
                )
                return _extract_content(r)

        return await loop.run_in_executor(None, _call)

    @staticmethod
    def _build_final_document(
        chapter_summaries: list[tuple[str, str]],
        title: str,
    ) -> str:
        """
        Assemble the final summary by concatenating all chapter summaries.
        No AI call — guaranteed zero information loss.
        """
        toc_lines = ["## Inhaltsverzeichnis\n"]
        for i, (ch_title, _) in enumerate(chapter_summaries, 1):
            toc_lines.append(f"{i}. {ch_title}")

        sections: list[str] = []
        for ch_title, ch_summary in chapter_summaries:
            sections.append(f"---\n\n## {ch_title}\n\n{ch_summary}")

        toc     = '\n'.join(toc_lines)
        content = '\n\n'.join(sections)
        return f"# Zusammenfassung: {title}\n\n{toc}\n\n{content}"

    # ------------------------------------------------------------------
    # Image handling
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_image_paths(md_text: str, images_dir: Path) -> str:
        """Fix image references where the AI dropped the leading underscore from filenames."""
        def _fix(m: re.Match) -> str:
            alt, filename = m.group(1), m.group(2)
            if (images_dir / filename).exists():
                return m.group(0)
            fixed = "_" + filename
            if (images_dir / fixed).exists():
                return f"![{alt}](images/{fixed})"
            return m.group(0)
        return re.sub(r'!\[([^\]]*)\]\(images/([^)]+)\)', _fix, md_text)

    # ------------------------------------------------------------------
    # (Image copy helper — kept below)
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
