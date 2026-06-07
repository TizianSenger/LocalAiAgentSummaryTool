"""
Manages per-folder AI settings stored as settings.json inside each folder.

Each folder can have its own system prompt, model choice, and summarization
parameters so the user can tailor the AI behavior per subject.
"""
import json
from pathlib import Path

# The default system prompt shown when a folder is first created.
# Written in German to match the target use-case (German university scripts).
_DEFAULT_SYSTEM_PROMPT = """Du bist ein hochspezialisierter Lernassistent für Universitätsstudenten.
Deine Aufgabe ist es, wissenschaftliche Lernscripte präzise und strukturiert zusammenzufassen.

Beachte folgende Regeln:
1. Behalte alle wichtigen Definitionen, Formeln und Konzepte vollständig bei
2. Strukturiere die Zusammenfassung klar mit Markdown-Überschriften (##, ###)
3. Bewahre mathematische Formeln exakt in LaTeX-Notation ($...$ oder $$...$$)
4. Behalte wichtige Code-Beispiele in Codeblöcken (```sprache ... ```)
5. Fasse Tabellen kompakt aber vollständig in Markdown-Tabellenformat zusammen
6. Erkläre komplexe Konzepte in klarer, verständlicher Sprache
7. Füge am Ende jedes Abschnitts eine kurze Merkhilfe oder Zusammenfassung hinzu"""


class SettingsManager:
    """Reads and writes the settings.json file inside a study folder."""

    FILENAME = "settings.json"

    @staticmethod
    def default_settings() -> dict:
        """
        Return the factory-default settings applied to every new folder.
        These are also used as fallback values when loading a partial settings file.
        """
        return {
            "ollama_model": "qwen2.5:14b",
            "system_prompt": _DEFAULT_SYSTEM_PROMPT,
            "summary_length": "medium",   # short | medium | long | comprehensive
            "language": "de",
            "include_images": True,
            "include_tables": True,
            "include_formulas": True,
            "include_code": True,
            "chunk_size": 3000,           # characters per summarization chunk
            "temperature": 0.3,           # low = more factual, high = more creative
            # Vision model for image analysis (requires a multimodal Ollama model)
            "use_vision": False,
            "vision_model": "llama3.2-vision:11b",
        }

    def load_settings(self, folder_name: str, base_dir: Path) -> dict:
        """
        Load a folder's settings.json, merging missing keys from defaults.
        Safe to call even if the file does not exist yet.

        Args:
            folder_name: Filesystem-safe folder name (subdirectory of base_dir)
            base_dir:    Root data directory

        Returns:
            Complete settings dict (guaranteed to have all default keys)
        """
        settings_path = base_dir / folder_name / self.FILENAME
        defaults = self.default_settings()

        if not settings_path.exists():
            return defaults

        try:
            with open(settings_path, encoding="utf-8") as f:
                saved = json.load(f)
            # Merge: saved values override defaults; missing keys use defaults
            return {**defaults, **saved}
        except (json.JSONDecodeError, OSError):
            return defaults

    def save_settings(self, folder_name: str, base_dir: Path, settings: dict) -> dict:
        """
        Persist a settings dict to the folder's settings.json.
        Only known keys (from defaults) are written to keep the file clean.

        Args:
            folder_name: Filesystem-safe folder name
            base_dir:    Root data directory
            settings:    Dict of settings to save (unknown keys are ignored)

        Returns:
            The filtered settings dict that was actually saved
        """
        settings_path = base_dir / folder_name / self.FILENAME
        defaults = self.default_settings()

        # Only persist keys we recognize to prevent junk accumulating in the file
        filtered = {key: settings.get(key, default) for key, default in defaults.items()}

        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(filtered, f, ensure_ascii=False, indent=2)

        return filtered
