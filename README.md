# StudyScript AI

Eine Desktop-Applikation zum intelligenten Zusammenfassen von Uni-Lernscripten mithilfe lokaler KI (Ollama). PDFs werden 1:1 in Markdown konvertiert und anschließend durch ein lokal laufendes Sprachmodell auf das Wesentliche reduziert.

---

## Features

- **Ordnerverwaltung** – Lernfächer als benannte Ordner anlegen
- **PDF-Import** – Drag & Drop oder Datei-Dialog, beliebige Seitenzahl (400–1500+ Seiten)
- **1:1 PDF → Markdown Konvertierung** – Bilder, Tabellen, Matheformeln, Code, Fließtext werden vollständig erhalten
- **KI-Zusammenfassung** – Lokales Ollama-Modell fasst das Script nach deinen Vorgaben zusammen
- **Pro-Ordner Einstellungen** – Eigener System-Prompt, Modellwahl, Zusammenfassungslänge, Temperatur
- **Echtzeit-Fortschritt** – Fortschrittsbalken via WebSocket während Konvertierung und KI-Verarbeitung
- **Markdown-Vorschau** – Konvertiertes Script und Zusammenfassung direkt in der App lesbar
- **GPU-Beschleunigung** – marker-pdf nutzt CUDA automatisch (RTX-Karten)

---

## Voraussetzungen

| Software | Version | Download |
|---|---|---|
| Python | 3.11 oder 3.12 | [python.org](https://www.python.org/downloads/) |
| Node.js | 18+ | [nodejs.org](https://nodejs.org) |
| Ollama | aktuell | [ollama.com](https://ollama.com) |
| Git | optional | [git-scm.com](https://git-scm.com) |

> **Wichtig:** Python 3.11 oder 3.12 verwenden. Python 3.13+ ist wegen eines Pillow-Konflikts mit `marker-pdf` nicht kompatibel.

---

## Installation

### 1. Repository klonen

```bash
git clone https://github.com/TizianSenger/LocalAiAgentSummaryTool.git
cd LocalAiAgentSummaryTool
```

### 2. Install-Skript ausführen

```batch
.\install.bat
```

Das Skript installiert automatisch:
- Alle Python-Pakete (`marker-pdf`, `pymupdf4llm`, `FastAPI`, `ollama`, …)
- PyTorch mit CUDA 12.8 GPU-Beschleunigung (falls NVIDIA GPU vorhanden, ~2–3 GB Download)
- Electron und seine Abhängigkeiten (`npm install`)

> Beim ersten Start der PDF-Konvertierung lädt `marker-pdf` automatisch KI-Modelle herunter (~2–4 GB). Das passiert einmalig.

### 3. Ollama-Modell installieren

#### Zusammenfassungs-Modell (Pflicht)

```bash
# Empfohlen – beste Qualität für akademische deutsche Texte
ollama pull qwen2.5:14b

# Alternativ – schneller, weniger VRAM
ollama pull qwen2.5:7b
```

#### Vision-Modell (optional, für Bildanalyse)

```bash
# Empfohlen – analysiert Diagramme, Graphen, Formeln aus dem PDF
ollama pull llama3.2-vision:11b
```

> Nach der Installation das Vision-Modell pro Ordner unter **⚙ Einstellungen → Bildanalyse** aktivieren.

---

## Modell-Empfehlungen

### Zusammenfassungs-Modelle

| Modell | VRAM | Qualität | Geschwindigkeit | Empfehlung |
|---|---|---|---|---|
| `qwen2.5:14b` | ~9 GB | ★★★★★ | mittel | **Empfohlen** |
| `qwen2.5:7b` | ~5 GB | ★★★★☆ | schnell | Schnellere Alternative |
| `gemma2:9b` | ~6 GB | ★★★★☆ | mittel | Google-Alternative |
| `llama3.1:8b` | ~5 GB | ★★★☆☆ | schnell | Meta-Alternative |
| `mistral:7b` | ~5 GB | ★★★☆☆ | sehr schnell | Wenn Speed wichtiger als Qualität |

> **VRAM-Richtwert:** GPU mit ≥8 GB → `qwen2.5:14b`. GPU mit ≥6 GB → `qwen2.5:7b` oder `gemma2:9b`.

### Vision-Modelle (Bildanalyse)

Benötigt nur wenn Bilder aus dem PDF inhaltlich verstanden werden sollen (Diagramme, Graphen, Abbildungen).

| Modell | VRAM | Qualität | Geschwindigkeit | Empfehlung |
|---|---|---|---|---|
| `llama3.2-vision:11b` | ~8 GB | ★★★★★ | mittel | **Empfohlen** |
| `llava:13b` | ~9 GB | ★★★★☆ | mittel | Gute Alternative |
| `llava:7b` | ~5 GB | ★★★☆☆ | schnell | Wenig VRAM |
| `moondream` | ~2 GB | ★★☆☆☆ | sehr schnell | Minimale Anforderungen |

> **Hinweis:** Text- und Vision-Modell laufen nacheinander, nicht gleichzeitig. Ein GPU mit 12 GB VRAM kann beide problemlos nutzen.

---

## Starten

```batch
.\start.bat
```

Die App startet den Python-Backend automatisch im Hintergrund und öffnet das Fenster. Beim ersten Start dauert es einige Sekunden bis der Backend-Server bereit ist.

---

## Benutzung

### Schritt 1 – Ordner erstellen

Klicke auf **„Neuer Ordner"** und vergib einen Namen (z. B. `Mathematik 2`).  
Jeder Ordner repräsentiert ein Lernfach.

### Schritt 2 – PDF hochladen

Öffne den Ordner und ziehe deine PDF-Datei in den Upload-Bereich oder klicke auf **„PDF auswählen"**.

### Schritt 3 – In Markdown konvertieren

Klicke auf **„In Markdown konvertieren"**. Das Script wird 1:1 umgewandelt:

| Inhalt | Ergebnis |
|---|---|
| Fließtext | Markdown-Paragraphen |
| Überschriften | `#`, `##`, `###` |
| Tabellen | Markdown-Tabellen |
| Matheformeln | LaTeX (`$...$`, `$$...$$`) |
| Code | Fenced Code Blocks (` ```sprache ``` `) |
| Bilder | Extrahierte PNG-Dateien, eingebettet per `![](images/...)` |

### Schritt 4 – KI-Zusammenfassung erstellen

Klicke auf **„KI-Zusammenfassung erstellen"**. Die App teilt das Markdown in Abschnitte auf, schickt jeden Abschnitt an Ollama und kombiniert die Ergebnisse zu einem finalen Lerndokument.

### Schritt 5 – Ergebnis lesen

Die Zusammenfassung ist direkt in der App im **Markdown-Viewer** lesbar. Beide Dokumente (Original-Konvertierung und Zusammenfassung) können über die Tabs oben umgeschaltet werden.

---

## KI-Einstellungen (pro Ordner)

Jeder Ordner hat eigene Einstellungen, erreichbar über **„⚙ Einstellungen"**:

| Einstellung | Beschreibung |
|---|---|
| **Ollama Modell** | Welches lokal installierte Modell verwendet wird |
| **System-Prompt** | Instruktionen für die KI (Ton, Schwerpunkte, Sprache, …) |
| **Länge** | Kurz (~20%) / Mittel (~40%) / Lang (~60%) / Vollständig (~80%) |
| **Temperatur** | 0 = präzise & faktisch · 1 = kreativ & variiert |
| **Chunk-Größe** | Wie viele Zeichen pro KI-Anfrage (Standard: 3000) |
| **Inhalte** | Bilder / Tabellen / Formeln / Code einzeln ein-/ausblenden |

---

## Dateistruktur auf der Festplatte

```
data/
└── {OrdnerName}/
    ├── meta.json          ← Name, Typ, Erstelldatum
    ├── settings.json      ← KI-Einstellungen dieses Ordners
    ├── original/
    │   └── script.pdf     ← dein hochgeladenes PDF
    ├── converted/
    │   ├── script.md      ← 1:1 Markdown-Konvertierung
    │   └── images/
    │       └── *.png      ← extrahierte Bilder
    └── summary/
        ├── summary.md     ← KI-generierte Zusammenfassung
        └── images/
            └── *.png      ← referenzierte Bilder
```

---

## Technischer Aufbau

```
LocalAiAgentSummaryTool/
├── backend/               ← Python / FastAPI
│   ├── main.py            ← API-Routen + WebSocket
│   ├── pdf_converter.py   ← PDF → Markdown (marker-pdf + pymupdf4llm)
│   ├── ollama_service.py  ← KI-Zusammenfassung (Map-Reduce)
│   ├── folder_manager.py  ← Ordner-CRUD + Datei-Upload
│   ├── settings_manager.py← Pro-Ordner Einstellungen
│   └── models.py          ← Pydantic-Datenmodelle
└── frontend/              ← Electron + anime.js
    ├── main.js            ← Electron Hauptprozess
    ├── preload.js         ← Sicherer IPC-Bridge
    └── src/
        ├── index.html     ← UI (alle Views)
        ├── styles/main.css← Dark-Theme, Animationen
        └── scripts/
            ├── app.js     ← App-Controller (State, Events)
            ├── api.js     ← HTTP-Client für das Backend
            └── animations.js ← anime.js Animationen
```

### Wie KI-Zusammenfassung intern funktioniert (Map-Reduce)

Große Scripte (400–1500 Seiten) passen nicht in ein einzelnes Kontextfenster. Deshalb:

1. **Split** – Das Markdown wird an Überschriften in Abschnitte (~3000 Zeichen) aufgeteilt
2. **Map** – Jeder Abschnitt wird einzeln an Ollama geschickt und zusammengefasst
3. **Reduce** – Alle Teilzusammenfassungen werden zu einem finalen, strukturierten Dokument kombiniert (inkl. Inhaltsverzeichnis)

---

## Häufige Probleme

**App öffnet sich, Backend startet nicht**  
→ Prüfe ob Python 3.11/3.12 installiert ist: `py -3.11 --version`

**„Keine Modelle gefunden" in den Einstellungen**  
→ Ollama muss laufen und mindestens ein Modell installiert sein: `ollama pull llama3.1`

**PDF-Konvertierung sehr langsam**  
→ Beim allerersten Mal lädt marker-pdf ~2–4 GB Modelle herunter. Danach deutlich schneller.

**GPU wird nicht genutzt (CPU-Auslastung sehr hoch)**  
→ PyTorch wurde ohne CUDA installiert. Fix: `py -3.11 -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128`

**Bildanalyse funktioniert nicht**  
→ Vision-Modell muss separat installiert sein: `ollama pull llama3.2-vision:11b`. In den Ordner-Einstellungen unter „Bildanalyse" aktivieren.

**Installation schlägt fehl (Pillow-Fehler)**  
→ Python 3.13 oder 3.14 wird nicht unterstützt. Python 3.11 oder 3.12 installieren.

---

## Lizenz

MIT
