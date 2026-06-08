"""
Generates an Obsidian-compatible vault from a converted Markdown document.

Each H2 section becomes its own note file. Wikilinks connect related notes.
An index file (000_Index.md) lists all notes as a Map of Content.

Vault layout:
    vault/
    ├── 000_Index.md
    ├── 001_Section_Title.md
    ├── 002_Another_Section.md
    └── images/
"""
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional


def _safe_filename(title: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|#^[\]{}]', '', title)
    safe = re.sub(r'\s+', '_', safe.strip())
    return safe[:60] or 'Abschnitt'


class VaultService:

    async def generate(
        self,
        safe_name: str,
        base_dir: Path,
        progress: Optional[Callable[[int, str], Awaitable[None]]] = None,
    ) -> dict:
        folder_path = base_dir / safe_name
        converted_dir = folder_path / "converted"
        vault_dir = folder_path / "vault"

        md_files = list(converted_dir.glob("*.md"))
        if not md_files:
            raise FileNotFoundError(
                "Kein konvertiertes Markdown gefunden. Bitte zuerst das PDF konvertieren."
            )

        await self._emit(progress, 5, "Lese Markdown-Dokument…")
        md_content = md_files[0].read_text(encoding="utf-8")
        doc_title = md_files[0].stem

        await self._emit(progress, 10, "Teile in Abschnitte auf…")
        sections = self._split_into_sections(md_content)

        if not sections:
            raise ValueError("Keine Abschnitte im Dokument gefunden.")

        await self._emit(progress, 15, f"{len(sections)} Abschnitte gefunden – erstelle Vault-Ordner…")

        if vault_dir.exists():
            shutil.rmtree(vault_dir)
        vault_dir.mkdir()
        (vault_dir / "images").mkdir()

        all_titles = [s["title"] for s in sections if s["title"]]
        filenames: list[tuple[str, str]] = []  # (title, filename_stem)

        for i, section in enumerate(sections):
            pct = 15 + int((i / len(sections)) * 70)
            title = section["title"] or f"Abschnitt_{i + 1}"
            stem = f"{i + 1:03d}_{_safe_filename(title)}"
            filenames.append((title, stem))

            await self._emit(progress, pct, f"Erstelle Notiz: {title}…")

            content = self._add_wikilinks(section["content"], all_titles, title)
            file_text = self._make_frontmatter(title) + content
            (vault_dir / f"{stem}.md").write_text(file_text, encoding="utf-8")

        await self._emit(progress, 88, "Erstelle Index-Datei…")
        index_text = self._make_index(doc_title, filenames)
        (vault_dir / "000_Index.md").write_text(index_text, encoding="utf-8")

        await self._emit(progress, 93, "Kopiere Bilder…")
        src_images = converted_dir / "images"
        copied = 0
        if src_images.exists():
            for img in src_images.iterdir():
                if img.is_file():
                    shutil.copy2(img, vault_dir / "images" / img.name)
                    copied += 1

        await self._emit(progress, 100,
            f"Vault erstellt! {len(sections)} Notizen, {copied} Bilder → {vault_dir}")

        return {
            "vault_path": str(vault_dir),
            "note_count": len(sections) + 1,
            "image_count": copied,
            "message": "Obsidian Vault erfolgreich erstellt.",
        }

    def list_files(self, safe_name: str, base_dir: Path) -> list[dict]:
        vault_dir = base_dir / safe_name / "vault"
        if not vault_dir.exists():
            return []
        files = []
        for f in sorted(vault_dir.glob("*.md")):
            raw = f.stem.lstrip("0123456789").lstrip("_")
            title = raw.replace("_", " ").strip() or f.stem
            files.append({
                "name": f.name,
                "stem": f.stem,
                "title": title,
                "size_kb": round(f.stat().st_size / 1024, 1),
            })
        return files

    # ------------------------------------------------------------------
    # Section splitting
    # ------------------------------------------------------------------

    # Matches "Lektion 5", "**Lektion 5**", etc.
    _LEKTION_RE = re.compile(r'^\*{0,2}Lektion\s+\d+\*{0,2}$', re.IGNORECASE)
    # Titles that are clearly document metadata (cover, subtitle, TOC)
    _SKIP_TITLES = frozenset({
        'STUDIENSKRIPT', 'Studienskript', 'Requirements Engineering',
        'Anhang 1', 'Anhang 2', 'Anhang 3',
    })

    def _split_into_sections(self, text: str) -> list[dict]:
        """
        Split into meaningful study sections.

        Handles quirks of lecture-style PDFs:
        - Skips near-empty cover / TOC / impressum sections
        - Renames "Lektion X" sections to their actual H3 topic title
        - Skips tiny filler sections (< 150 chars)
        """
        lines = text.split("\n")
        raw: list[dict] = []
        current_title: Optional[str] = None
        current_lines: list[str] = []

        def _flush():
            content = "\n".join(current_lines).strip()
            if current_title is not None:
                raw.append({"title": current_title, "content": content})

        for line in lines:
            m_h2 = re.match(r"^## (.+)$", line)
            m_h1 = re.match(r"^# (.+)$", line)
            heading = m_h2 or m_h1
            if heading:
                _flush()
                current_title = re.sub(r"\*+", "", heading.group(1)).strip()
                current_lines = []
            else:
                current_lines.append(line)
        _flush()

        sections: list[dict] = []
        for s in raw:
            title = s["title"]
            content = s["content"]

            # 1. Skip metadata / cover / appendix sections without real content
            if title in self._SKIP_TITLES and len(content) < 1500:
                continue

            # 2. Skip near-empty sections (just a heading, a blank line, an image)
            text_only = re.sub(r'!\[.*?\]\(.*?\)', '', content)
            text_only = re.sub(r'\s+', ' ', text_only).strip()
            if len(text_only) < 150:
                continue

            # 3. If title is "Lektion X", use the first H3 as the real title
            if self._LEKTION_RE.match(title):
                h3 = re.search(r"^### (.+)$", content, re.MULTILINE)
                if h3:
                    title = re.sub(r"\*+", "", h3.group(1)).strip()

            sections.append({"title": title, "content": content})

        return sections

    # ------------------------------------------------------------------
    # Wikilinks + frontmatter
    # ------------------------------------------------------------------

    def _add_wikilinks(self, content: str, all_titles: list[str], current_title: str) -> str:
        for title in all_titles:
            if title == current_title or not title or len(title) < 5:
                continue
            escaped = re.escape(title)
            # Replace only the first occurrence, not inside existing [[...]] or code
            content = re.sub(
                rf'(?<!\[\[)(?<![`\w]){escaped}(?![`\w])(?!\]\])',
                f"[[{title}]]",
                content,
                count=1,
            )
        return content

    def _make_frontmatter(self, title: str) -> str:
        tag = re.sub(r"[^\w]", "-", title.split()[0].lower()) if title.split() else "section"
        today = datetime.now().strftime("%Y-%m-%d")
        return (
            f"---\n"
            f"title: \"{title}\"\n"
            f"tags: [studyscript, {tag}]\n"
            f"created: {today}\n"
            f"---\n\n"
            f"# {title}\n\n"
        )

    def _make_index(self, doc_title: str, filenames: list[tuple[str, str]]) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        lines = [
            "---",
            f'title: "Index – {doc_title}"',
            "tags: [index, studyscript]",
            f"created: {today}",
            "---",
            "",
            f"# 📚 Index: {doc_title}",
            "",
            f"> Vault generiert am {datetime.now().strftime('%d.%m.%Y um %H:%M')}",
            "",
            "## Alle Notizen",
            "",
        ]
        for title, stem in filenames:
            lines.append(f"- [[{stem}|{title}]]")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    async def _emit(callback: Optional[Callable], percent: int, message: str):
        if callback:
            await callback(percent, message)
