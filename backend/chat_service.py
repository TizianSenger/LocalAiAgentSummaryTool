"""
AI chat using vault files as knowledge source.

For each user message:
  1. Load all .md files from vault/
  2. Score each file by keyword overlap with the query
  3. Send top-N most relevant files as context to the AI
  4. Return AI answer + list of source files used
"""
import re
from pathlib import Path


_STOP_WORDS = {
    "der", "die", "das", "ein", "eine", "und", "oder", "ist", "sind", "mit",
    "von", "zu", "in", "im", "für", "auf", "bei", "wie", "was", "ich", "du",
    "er", "sie", "es", "wir", "kann", "wird", "haben", "sein", "nicht", "auch",
    "the", "a", "an", "and", "or", "is", "are", "with", "of", "to", "in", "what",
    "how", "why", "when", "where", "who", "which", "this", "that", "these",
}


class ChatService:

    def chat(
        self,
        safe_name: str,
        base_dir: Path,
        message: str,
        history: list[dict],
        settings: dict,
    ) -> dict:
        """
        Answer a question using vault content as context.

        Returns:
            dict with keys: "answer" (str), "sources" (list[str])
        """
        vault_dir = base_dir / safe_name / "vault"
        if not vault_dir.exists():
            return {
                "answer": "Kein Vault vorhanden. Bitte zuerst den Obsidian Vault erstellen.",
                "sources": [],
            }

        vault_files = self._load_vault(vault_dir)
        if not vault_files:
            return {
                "answer": "Der Vault ist leer. Bitte zuerst den Vault generieren.",
                "sources": [],
            }

        relevant = self._find_relevant(message, vault_files, top_n=4)
        context = self._build_context(relevant)
        sources = [f["title"] for f in relevant]

        if settings.get("ai_provider", "ollama") == "claude":
            answer = self._call_claude(message, history, context, settings)
        else:
            answer = self._call_ollama(message, history, context, settings)

        return {"answer": answer, "sources": sources}

    # ------------------------------------------------------------------
    # Vault loading + retrieval
    # ------------------------------------------------------------------

    def _load_vault(self, vault_dir: Path) -> list[dict]:
        files = []
        for f in sorted(vault_dir.glob("*.md")):
            try:
                raw = f.read_text(encoding="utf-8")
                # Strip YAML frontmatter
                content = re.sub(r"^---\n.*?\n---\n", "", raw, flags=re.DOTALL).strip()
                raw_title = f.stem.lstrip("0123456789").lstrip("_").replace("_", " ").strip()
                files.append({
                    "name": f.stem,
                    "title": raw_title or f.stem,
                    "content": content,
                    "words": set(re.findall(r"\w{3,}", content.lower())),
                })
            except Exception:
                pass
        return files

    def _find_relevant(self, query: str, vault_files: list[dict], top_n: int = 4) -> list[dict]:
        query_words = set(re.findall(r"\w{3,}", query.lower())) - _STOP_WORDS
        if not query_words:
            return vault_files[:top_n]

        scored = []
        for f in vault_files:
            overlap = len(query_words & f["words"])
            score = overlap / len(query_words)
            scored.append((score, f))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:top_n]]

    def _build_context(self, files: list[dict]) -> str:
        parts = []
        for f in files:
            # Limit each file to 2500 chars so context stays reasonable
            snippet = f["content"][:2500]
            parts.append(f"=== {f['title']} ===\n{snippet}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # AI calls
    # ------------------------------------------------------------------

    @staticmethod
    def _system_prompt(context: str) -> str:
        return (
            "Du bist ein Lernassistent für Universitätsstudenten. "
            "Beantworte Fragen ausschließlich auf Basis des folgenden Lernmaterials. "
            "Erkläre klar und einfach, ohne Fachjargon. "
            "Falls die Antwort nicht im Material steht, sage das ehrlich.\n\n"
            f"LERNMATERIAL:\n{context}"
        )

    def _call_ollama(
        self, message: str, history: list[dict], context: str, settings: dict
    ) -> str:
        import ollama

        messages = [{"role": "system", "content": self._system_prompt(context)}]
        for h in history[-8:]:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": message})

        response = ollama.chat(
            model=settings.get("ollama_model", "qwen2.5:14b"),
            messages=messages,
            options={"temperature": settings.get("temperature", 0.3), "num_ctx": 8192},
        )
        if hasattr(response, "message"):
            return response.message.content
        return response["message"]["content"]

    def _call_claude(
        self, message: str, history: list[dict], context: str, settings: dict
    ) -> str:
        import os
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or api_key == "dein-api-key-hier":
            raise RuntimeError("ANTHROPIC_API_KEY nicht in .env konfiguriert.")

        claude_msgs = []
        for h in history[-8:]:
            claude_msgs.append({"role": h["role"], "content": h["content"]})
        claude_msgs.append({"role": "user", "content": message})

        try:
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model=settings.get("claude_model", "claude-haiku-4-5-20251001"),
                max_tokens=2048,
                temperature=settings.get("temperature", 0.3),
                system=self._system_prompt(context),
                messages=claude_msgs,
            )
            return msg.content[0].text
        except anthropic.AuthenticationError:
            raise RuntimeError("Claude API: Ungültiger API Key.")
        except anthropic.RateLimitError:
            raise RuntimeError("Claude API: Rate Limit erreicht.")
        except Exception as e:
            raise RuntimeError(f"Claude API Fehler: {e}")
