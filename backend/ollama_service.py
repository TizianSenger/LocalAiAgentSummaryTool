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

        # Blocking Ollama/Claude calls run off the event loop via executor.
        # IMPORTANT: Two-pass strategy to avoid constant model swapping in RAM.
        # Each model swap (vision ↔ text) costs ~8-14 GB RAM reload. With 74 chunks
        # and interleaved calls that would happen 74 times. Two passes = 1 swap total.
        loop = asyncio.get_running_loop()
        chunk_summaries: list[str] = []

        use_vision        = settings.get("use_vision", False)
        vision_provider   = settings.get("vision_provider", "ollama")
        vision_model      = settings.get("vision_model", "llama3.2-vision:11b")
        claude_vis_model  = settings.get("claude_vision_model", "claude-haiku-4-5-20251001")
        active_vis_label  = f"Claude ({claude_vis_model})" if vision_provider == "claude" else vision_model

        # --- Pass 1: Vision enrichment (one image at a time for granular progress) ---
        enriched_chunks = list(chunks)
        if use_vision:
            # Collect all (chunk_idx, alt_text, filename) triples up front
            image_jobs: list[tuple[int, str, str]] = []
            for ci, chunk in enumerate(chunks):
                for m in re.finditer(r'!\[([^\]]*)\]\(images/([^)]+)\)', chunk):
                    image_jobs.append((ci, m.group(1), m.group(2)))

            if image_jobs:
                total_images = len(image_jobs)
                unique_chunks = len({j[0] for j in image_jobs})
                await self._emit(progress, 10,
                    f"Bildanalyse: {total_images} Bild(er) in {unique_chunks} Abschnitt(en) via {active_vis_label}…")

                # descriptions[chunk_idx][filename] = description text
                import time
                descriptions: dict[int, dict[str, str]] = {}

                for img_seq, (chunk_idx, alt, filename) in enumerate(image_jobs):
                    if cancel_check and cancel_check():
                        await self._emit(progress, 0, "⚠ Vorgang durch Benutzer abgebrochen.")
                        raise RuntimeError("Abgebrochen")

                    pct = 10 + int((img_seq / total_images) * 25)
                    await self._emit(progress, pct,
                        f"🖼 Bild {img_seq + 1}/{total_images}: {filename} (Abschnitt {chunk_idx + 1}/{total})…")

                    image_path = converted_dir / "images" / filename
                    t0 = time.monotonic()
                    if vision_provider == "claude":
                        desc = await loop.run_in_executor(
                            None, self._describe_image_claude, image_path, claude_vis_model, alt
                        )
                    else:
                        desc = await loop.run_in_executor(
                            None, self._describe_image_ollama, image_path, vision_model, alt
                        )
                    elapsed = time.monotonic() - t0

                    if chunk_idx not in descriptions:
                        descriptions[chunk_idx] = {}
                    descriptions[chunk_idx][filename] = desc

                    if desc:
                        await self._emit(progress, pct,
                            f"  ✓ {filename} ({elapsed:.1f}s): {desc[:80]}…")
                    else:
                        await self._emit(progress, pct,
                            f"  ⚠ {filename} ({elapsed:.1f}s): Keine Beschreibung – Bild übersprungen")

                # Apply collected descriptions back into the chunks
                for ci, descs in descriptions.items():
                    def _apply(chunk: str, _descs: dict = descs) -> str:
                        def replace(m: re.Match) -> str:
                            d = _descs.get(m.group(2), "")
                            if d:
                                label = m.group(1) or m.group(2)
                                return f'\n> 📷 **Abbildung – {label}:** {d}\n'
                            return m.group(0)
                        return re.sub(r'!\[([^\]]*)\]\(images/([^)]+)\)', replace, chunk)
                    enriched_chunks[ci] = _apply(chunks[ci])

        # --- Pass 2: Text summarization (loads text model once for all chunks) ---
        await self._emit(progress, 35, f"Starte Textzusammenfassung für {total} Abschnitte…")
        for i, chunk in enumerate(enriched_chunks):
            if cancel_check and cancel_check():
                await self._emit(progress, 0, "⚠ Vorgang durch Benutzer abgebrochen.")
                raise RuntimeError("Abgebrochen")

            pct = 35 + int((i / total) * 50)
            char_count = len(chunk)
            preview = chunk.replace('\n', ' ').strip()[:90]
            await self._emit(progress, pct,
                f"Fasse Abschnitt {i + 1} von {total} zusammen… ({char_count} Zeichen)")
            await self._emit(progress, pct, f"↳ {preview}…")

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

        final_md = await self._merge_async(chunk_summaries, settings, document_title, progress)

        # Fix any image paths where the AI dropped the leading underscore
        final_md = self._fix_image_paths(final_md, converted_dir / "images")

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
                # Keep the original image reference AND add a description caption below.
                # This way the text summarizer can still include the image in its output.
                return f'![{alt}](images/{filename})\n> 📷 **{label}:** {desc}\n'
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

    async def _merge_async(
        self,
        summaries: list[str],
        settings: dict,
        title: str,
        progress: Optional[Callable[[int, str], Awaitable[None]]],
    ) -> str:
        """
        Hierarchical merge with live progress updates.

        Reduces chunk summaries in passes of BATCH until ≤ BATCH remain,
        then does one final merge. Intermediate calls use a tight token budget
        (2000) so they stay compact; the final call gets the full 8192 budget.
        Each API call runs in an executor so the event loop stays unblocked and
        progress messages reach the UI between batches.
        """
        loop = asyncio.get_running_loop()
        provider = settings.get("ai_provider", "ollama")
        BATCH = 8

        _IMG_HINT = (
            "Behalte Bild-Referenzen als `![alt](images/dateiname)` – "
            "kopiere den Dateinamen exakt (inkl. führendem Unterstrich, z.B. `_page_15_Picture_2.jpeg`). "
            "Keine Bildbeschreibungen darunter."
        )

        # Build a provider-specific blocking call function
        if provider == "claude":
            import os as _os
            import anthropic as _anthropic
            api_key = _os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key or api_key == "dein-api-key-hier":
                raise RuntimeError("ANTHROPIC_API_KEY nicht in .env konfiguriert.")
            model      = settings.get("claude_model", "claude-haiku-4-5-20251001")
            temperature = settings.get("temperature", 0.3)
            system_prompt = settings.get("system_prompt", "")

            def _call(prompt: str, max_tok: int) -> str:
                try:
                    client = _anthropic.Anthropic(api_key=api_key)
                    msg = client.messages.create(
                        model=model, max_tokens=max_tok, temperature=temperature,
                        system=system_prompt,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return msg.content[0].text
                except _anthropic.AuthenticationError:
                    raise RuntimeError("Claude API: Ungültiger API Key. Bitte .env prüfen.")
                except _anthropic.BadRequestError as e:
                    if "credit" in str(e).lower() or "balance" in str(e).lower():
                        raise RuntimeError("Claude API: Kein Guthaben. Bitte unter console.anthropic.com aufladen.")
                    raise RuntimeError(f"Claude API Fehler: {e}")
                except _anthropic.RateLimitError:
                    raise RuntimeError("Claude API: Rate Limit erreicht. Bitte kurz warten.")
                except Exception as e:
                    raise RuntimeError(f"Claude API nicht erreichbar: {e}")
        else:
            import ollama as _ollama
            model       = settings.get("ollama_model", "qwen2.5:14b")
            temperature = settings.get("temperature", 0.3)
            system_prompt = settings.get("system_prompt", "")

            def _call(prompt: str, max_tok: int) -> str:
                resp = _ollama.chat(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": prompt},
                    ],
                    options={"temperature": temperature, "num_ctx": 16384, "num_predict": max_tok},
                )
                return _extract_content(resp)

        # Calculate total rounds needed for display
        n, total_rounds = len(summaries), 0
        _n = len(summaries)
        while _n > BATCH:
            _n = -(-_n // BATCH)   # ceiling division
            total_rounds += 1
        total_rounds += 1  # +1 for the final merge

        current = list(summaries)
        round_idx = 0

        # Intermediate reduce rounds
        while len(current) > BATCH:
            round_idx += 1
            batches   = [current[i:i + BATCH] for i in range(0, len(current), BATCH)]
            pct       = 88 + int((round_idx / total_rounds) * 9)   # 88 → 97 %
            await self._emit(progress, pct,
                f"Merge-Runde {round_idx}/{total_rounds}: {len(batches)} Batch(es) à max. {BATCH} Abschnitte…")

            new_current: list[str] = []
            for bi, batch in enumerate(batches):
                await self._emit(progress, pct,
                    f"  Batch {bi + 1}/{len(batches)}: {len(batch)} Abschnitte → komprimiere…")
                result = await loop.run_in_executor(None, _call,
                    "Fasse diese Teilzusammenfassungen zu einem kompakten Zwischenergebnis zusammen. "
                    f"Entferne Duplikate, behalte wichtige Konzepte, Formeln, Tabellen. {_IMG_HINT}\n\n"
                    + "\n\n---\n\n".join(batch)
                    + "\n\nAntworte nur mit dem Markdown.",
                    2000,   # tight budget keeps intermediates short
                )
                new_current.append(result)
                await self._emit(progress, pct,
                    f"  ✓ Batch {bi + 1}/{len(batches)} fertig ({len(result)} Zeichen)")
            current = new_current

        # Final merge
        round_idx += 1
        await self._emit(progress, 97,
            f"Finaler Merge (Runde {round_idx}/{total_rounds}): {len(current)} Abschnitt(e) → Gesamtdokument…")
        combined = "\n\n---\n\n".join(current)
        body = await loop.run_in_executor(None, _call,
            "Das sind die Teilzusammenfassungen eines Lernscripts. "
            "Erstelle daraus EIN kompaktes, gut strukturiertes Lerndokument.\n"
            "- Füge ein Markdown-Inhaltsverzeichnis am Anfang ein\n"
            "- Entferne Duplikate konsequent und verbinde verwandte Themen\n"
            "- Das Ergebnis muss KÜRZER sein als die Eingabe – nur das Wesentliche in einfachen Worten\n"
            f"- {_IMG_HINT}\n"
            "- Behalte Formeln, Tabellen und Codeblöcke\n\n"
            f"{combined}\n\n"
            "Antworte nur mit dem finalen Markdown-Dokument.",
            8192,   # full budget for final output
        )
        await self._emit(progress, 99, "Finales Dokument erstellt.")
        return f"# Zusammenfassung: {title}\n\n{body}"

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
