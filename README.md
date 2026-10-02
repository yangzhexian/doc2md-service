# Document to Markdown Converter Service

A local HTTP microservice that converts PDF, DOCX, PPTX, XLSX, HTML, CSV,
images, and other formats to Markdown. Powered by
**[MinerU](https://github.com/opendatalab/MinerU)** (primary) and
**[MarkItDown](https://github.com/microsoft/markitdown)** (fallback).

Tracked upstream versions: **MinerU >= 4.0, < 5** and **markitdown >= 0.1.8**.

> **Breaking (4.0.0):** MinerU 3.x request fields (`backend`, `effort`,
> `method`, `lang`, `formula_enable`, `table_enable`, `server_url`,
> `start_page`, `end_page`) were replaced by `tier` / `pages` / `remote`.
> Re-download models with `./update.sh` — 3.x model trees are not recognized.

## Features

- **High-quality document conversion** — MinerU 4.x quality tiers handle
  complex layouts, math formulas, tables, and multi-column papers.
- **Quality tiers** — `flash` (fast preview / native docs), `basic`
  (OCR / formula / table), `standard` (complex layout, default),
  `advanced` (hard documents, higher compute). PDF and images support all
  four tiers; Office / HTML / CSV / EPUB and similar formats parse as
  whole-document `flash`.
- **Native multi-format MinerU** — PDF, images, DOCX/PPTX/XLSX, HTML, CSV/TSV,
  RTF, OpenDocument, EPUB, OFD route to MinerU; MarkItDown covers the rest
  and acts as automatic fallback.
- **Remote inference** — pass `remote: true` to offload MinerU inference
  (`--remote`).
- **Automatic fallback** — Falls back to MarkItDown if MinerU conversion fails
  (`fallback: true`, `fallback_from: "mineru"`).
  PDF page selections require MinerU; failures return an error instead of
  falling back to whole-document conversion.
- **Batch processing** — Convert an entire folder of documents at once.
- **Page ranges** — Re-convert just a slice of a long paper with the 4.0
  page-spec syntax (`pages: "1-5,8"` or `"r3-r1"`).
- **Predictable output layout** — Every file is saved to
  `<output_dir>/<filename>.docs2md/<stem>.md` with extracted images in
  `<output_dir>/<filename>.docs2md/images/`.
  The complete source filename separates outputs such as `report.pdf` and
  `report.docx`; very long directory names use a shortened prefix and digest.
- **Lightweight API responses** — The API returns status, engine, and the saved
  file path; it never returns the full Markdown content in the JSON body.
- **Three API modes** — Convert by local file path, file upload, or folder path.
- **One-click start** — `start.sh` (Linux/macOS) and `start.bat` (Windows)
  handle venv creation, dependency install, and service launch. Dependencies
  are reinstalled automatically whenever `requirements.txt` changes.
- **Swagger UI** — Interactive API docs at `/docs`.
- **Health check** — `GET /health` reports service status, per-tier model
  readiness, and GPU status.
- **Agent skill** — Includes a Claude Code skill (`.claude/skills/docs2md/`)
  so AI agents can call the service to convert documents.

## Requirements

| Requirement | Minimum |
|---|---|
| Operating System | Linux / Windows / macOS 14+ |
| Python | 3.10 – 3.14 (MinerU 4.x requires `>=3.10,<3.15`) |
| RAM | 16 GB (32 GB recommended) |
| Disk (free space) | 20 GB (SSD recommended) |
| GPU VRAM (optional) | 4 GB for GPU acceleration |
| MinerU | `>= 4.0, < 5` (installed automatically) |
| MarkItDown | `>= 0.1.8, < 0.2` (installed automatically) |

GPU users who want Torch acceleration can install the full extra themselves:
`pip install 'mineru[full]'` (the base `mineru` package is CPU/ONNX friendly).

## Quick Start

### 1. Clone

```bash
git clone https://github.com/yangzhexian/doc2md-service.git
cd doc2md-service
```

### 2. Download MinerU models

Use the included updater to download the model weights into the project-local
`mineru_models/` directory:

```bash
# Linux / macOS
./update.sh                          # standard tier (small + VLM, default)
./update.sh modelscope               # force ModelScope
./update.sh auto --tier basic        # small models only (~0.8 GB)

# Windows
update.bat                           # standard tier
update.bat modelscope                # force ModelScope
update.bat auto --tier basic         # small models only
```

| Tier | Size | Models |
|---|---|---|
| `flash` | — | none (text layer / native document parse) |
| `basic` | ~0.8 GB | small models (layout / formula / OCR / table) |
| `standard` | ~2 GB | small + VLM (default) |
| `advanced` | ~2 GB | same package as `standard`, higher inference cost |

The updater calls `mineru-kit models download --tier <tier>` and
`mineru-kit models verify --tier <tier>`, then writes `config/mineru.yaml`
so the service uses the project-local weights.
On Windows, the generated config selects the llama.cpp backend and the updater
downloads its GGUF model and projector. MinerU 4.0.5's LMDeploy backend fails
on some Windows installations when it loads the vision model. To choose a
different backend, set `DOCS2MD_MINERU_VLM_ENGINE` to `auto`, `lmdeploy`,
`vllm`, `mlx`, or `llama-cpp` before starting or updating the service.

> **Note:** MinerU 4.x model packages differ from 3.x. Re-run `./update.sh`
> after upgrading — old `mineru_models/` trees are not recognized.

### 3. Start

```bash
./start.sh      # Linux / macOS
start.bat       # Windows (background, close CMD safely)
start.vbs       # Windows (completely silent, no window)
```

The script handles everything automatically — virtual environment, dependencies,
and service launch. Pass a port number to change from the default 8000:
`./start.sh 9090`.  On Windows, use `stop.bat` to stop the background service.

On Linux/macOS, `./start.sh --port 9090 --host 0.0.0.0` selects the listening
address. Use `./start.sh --setup-only` to prepare dependencies without
starting a server.

Open **[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)** for the interactive Swagger UI.

### 4. (Optional) Autostart on Login / Boot

The service can be configured to start automatically when you log in,
so you never need to run `start.sh` / `start.bat` manually.

#### Linux (systemd user service)

```bash
./scripts/install-autostart.sh        # default port 8000
./scripts/install-autostart.sh 9090   # custom port
```

This creates a **systemd user service** that starts on login and
restarts automatically on failure. Manage it with:

```bash
systemctl --user start docs2md        # start now
systemctl --user stop docs2md         # stop
systemctl --user status docs2md       # check status
systemctl --user disable docs2md      # remove autostart
```

The service logs to the systemd journal:

```bash
journalctl --user -u docs2md -f       # follow logs
```

The installer waits for dependency setup to finish before enabling the
service. The systemd installer requires Linux with systemd; on macOS, use
`start.sh` directly or configure a launchd job separately.

#### Windows (Startup folder)

```cmd
scripts\install-autostart.bat         # default port 8000
scripts\install-autostart.bat 9090    # custom port
```

This creates a small batch file in your **Windows Startup folder**
(`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`) that runs
`start.vbs` silently on login — no console window appears.

To remove autostart on Windows, delete `docs2md.bat` from your Startup folder.

## How MinerU Models Are Configured

This project stores MinerU model weights locally in the `mineru_models/`
directory so that no network access is needed at runtime.

On startup, the service writes `config/mineru.yaml`:

```yaml
model:
  source: local
  base_dir: <project>/mineru_models
  small_backend: auto
  vlm:
    engine: llama-cpp  # Windows default; auto on other platforms
```

and sets `MINERU_CONFIG` to point at it so the MinerU 4.x CLI uses the
project-local models. (MinerU 4.x no longer reads `config/mineru.json`.)

This means the service is **self-configuring** — you just need to ensure
`mineru_models/` exists with the downloaded model files.

### Model directory structure

```
mineru_models/
├── MinerU-4_models_torch/        # or MinerU-4_models_onnx/
├── MinerU2.5-Pro-2605-1.2B/    # LMDeploy / vLLM weights
└── MinerU2.5-Pro-2605-1.2B-GGUF/ # llama.cpp model and projector
```

## Choosing a Quality Tier

The `tier` request field selects the MinerU quality / cost trade-off:

| Tier | Local models | Quality | Notes |
|---|---|---|---|
| `flash` | none | fast | PDF text layer + native document parse; scans use Flash OCR |
| `basic` | small | good | OCR / formula / table with ONNX or Torch small models |
| `standard` *(default)* | small + VLM | **best** | Complex layouts, academic papers |
| `advanced` | small + VLM | **best+** | Hard documents; higher inference cost |

PDF and images support every tier. Office / OpenDocument / RTF / EPUB / OFD /
HTML / CSV-TSV are whole-document `flash` parses regardless of the requested
tier. Pure text (`.txt` / `.md`) is not handled by MinerU.

For academic papers (math-heavy, dense tables, complex two-column layouts),
download the standard package (`./update.sh`) and keep the default
`tier=standard`. Use `tier=advanced` only when `standard` is not enough.

### Page specs

| Spec | Meaning |
|---|---|
| `all` | whole document (default) |
| `1-5` | pages 1 through 5 (1-based, inclusive) |
| `1-5,8` | pages 1–5 plus page 8 |
| `r3-r1` | 3rd-from-end through last page |

Page subsets are supported only for PDF inputs with MinerU. Other formats
and explicit MarkItDown requests require `pages: "all"`. If MinerU fails
for a page subset, the API returns an error and skips whole-document fallback.

## API Reference

### GET /health

Returns service status, registered engines, quality tiers, and per-tier model
readiness.

**Response:**
```json
{
  "status": "ok",
  "engines": ["mineru", "markitdown"],
  "default_engine": "auto",
  "tiers": ["advanced", "basic", "flash", "standard"],
  "models_ready": {
    "flash": true,
    "basic": true,
    "standard": true,
    "advanced": true
  },
  "cuda_available": true
}
```

- `models_ready` — whether the selected backend has its complete required
  model files and runtime dependencies (`flash` is always ready).

---

### POST /convert/path

Convert a file by its local absolute path. Results are saved to
`<output_dir>/<filename>.docs2md/<stem>.md` by default, with images (if any) in
`<output_dir>/<filename>.docs2md/images/`.

**Request (`application/json`):**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_path` | string | yes | — | Absolute path to the file |
| `output_dir` | string | no | parent of `file_path` | Base output directory |
| `engine` | string | no | by extension | Engine override: `mineru`, `markitdown` |
| `tier` | string | no | `"standard"` | MinerU quality tier: `flash`, `basic`, `standard`, `advanced` |
| `remote` | bool | no | `false` | Use remote MinerU inference (`--remote`) |
| `pages` | string | no | `"all"` | Page spec: `all`, `1-5,8`, `r3-r1` (1-based) |

**Response:**
```json
{
  "success": true,
  "engine": "mineru",
  "output_path": "/path/to/output/document.pdf.docs2md/document.md",
  "output_dir": "/path/to/output",
  "images_dir": "/path/to/output/document.pdf.docs2md/images",
  "fallback": false,
  "fallback_from": null,
  "message": "Saved to /path/to/output/document.pdf.docs2md/document.md"
}
```

The Markdown content is **not** returned in the response. Read it from
`output_path`.

---

### POST /convert/upload

Upload a file for conversion.

**Request (multipart/form-data):**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `file` | file | yes | — | File to convert |
| `output_dir` | string | no | `<project_root>/output/` | Base output directory |
| `engine` | string | no | by extension | Engine override: `mineru`, `markitdown` |
| `tier` | string | no | `"standard"` | MinerU quality tier |
| `remote` | bool | no | `false` | Use remote MinerU inference |
| `pages` | string | no | `"all"` | Page spec |

The default `output_dir` for uploads is the project `output/` directory, or the
value of the `DOCS2MD_UPLOAD_OUTPUT_DIR` environment variable. This prevents
results from being lost when the upload temp directory is cleaned up.

**Response:** Same as `/convert/path`.

---

### POST /convert/folder

Batch-convert all supported files in a folder. Results are saved to
`<output_dir>/<filename>.docs2md/<stem>.md` by default. Accepted extensions are the union
of every registered engine.

**Request (`application/json`):**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `folder_path` | string | yes | — | Absolute path to the folder |
| `output_dir` | string | no | `folder_path` | Base output directory |
| `engine` | string | no | by extension | Engine override |
| `tier` | string | no | `"standard"` | MinerU quality tier |
| `remote` | bool | no | `false` | Use remote MinerU inference |
| `pages` | string | no | `"all"` | Page spec |

**Response:**
```json
{
  "folder": "/path/to/docs",
  "output_dir": "/path/to/docs",
  "results": [
    {
      "file": "/path/to/docs/paper.pdf",
      "status": "ok",
      "engine": "mineru",
      "output_path": "/path/to/docs/paper.pdf.docs2md/paper.md",
      "images_dir": "/path/to/docs/paper.pdf.docs2md/images",
      "fallback": false,
      "fallback_from": null
    }
  ]
}
```

---

### Error Responses

**404 — File not found:**
```json
{ "detail": "File not found: /path/to/nonexistent.pdf" }
```

**400 — Invalid request or missing configuration:**
```json
{ "detail": "MinerU models for tier 'standard' are missing. Download them with ./update.sh --tier standard (or update.bat --tier standard)." }
```

Invalid `tier` / `pages` values also return 400 (or 422 for JSON body
validation) with an actionable message.

**409 — Output not writable:**
```json
{ "detail": "Unable to write conversion output '...': ... Choose a writable output_dir ..." }
```

**500 — Conversion error (after fallback):**
```json
{ "detail": "mineru error: ... markitdown error: ..." }
```

## Supported File Formats

| Category | Extensions | Engine |
|---|---|---|
| PDF | `.pdf` | MinerU → MarkItDown (fallback) |
| Word | `.docx`, `.doc` | MinerU (`flash`) → MarkItDown |
| PowerPoint | `.pptx`, `.ppt` | MinerU (`flash`) → MarkItDown |
| Excel | `.xlsx`, `.xls` | MinerU (`flash`) → MarkItDown |
| Web | `.html`, `.htm` | MinerU (`flash`) → MarkItDown |
| Data | `.csv`, `.tsv` | MinerU (`flash`) → MarkItDown |
| OpenDocument | `.odt`, `.ods`, `.odp` | MinerU (`flash`) → MarkItDown |
| eBooks | `.epub` | MinerU (`flash`) → MarkItDown |
| Other docs | `.rtf`, `.ofd`, `.ipynb`, `.msg` | MarkItDown |
| Images | `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.webp` | MinerU → MarkItDown |
| Text / markup | `.txt`, `.json`, `.xml` | MarkItDown |

## Usage Examples

### Python

```python
import requests

# Convert a single file
resp = requests.post(
    "http://127.0.0.1:8000/convert/path",
    json={
        "file_path": "/absolute/path/to/paper.pdf",
        "output_dir": "/custom/output/dir",  # optional
    },
)
data = resp.json()
print(f"Engine: {data['engine']}, Output: {data['output_path']}")
with open(data["output_path"], "r", encoding="utf-8") as f:
    print(f.read()[:200])

# Academic paper at maximum quality
resp = requests.post(
    "http://127.0.0.1:8000/convert/path",
    json={
        "file_path": "/absolute/path/to/paper.pdf",
        "tier": "advanced",
    },
)

# Re-convert only pages 3-8 of a long paper
resp = requests.post(
    "http://127.0.0.1:8000/convert/path",
    json={
        "file_path": "/absolute/path/to/thesis.pdf",
        "pages": "3-8",
    },
)

# Batch-convert a folder
resp = requests.post(
    "http://127.0.0.1:8000/convert/folder",
    json={"folder_path": "/absolute/path/to/docs/"},
)
for item in resp.json()["results"]:
    print(f"{item['file']}: {item['status']} ({item['engine']})")

# Convert by file upload
with open("/path/to/document.docx", "rb") as f:
    resp = requests.post(
        "http://127.0.0.1:8000/convert/upload",
        files={"file": f},
        data={"engine": "markitdown"},
    )
print(f"Saved to: {resp.json()['output_path']}")
```

### curl

```bash
# Single file
curl -X POST http://127.0.0.1:8000/convert/path \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/path/to/document.pdf"}'

# Single file with custom output directory and quality tier
curl -X POST http://127.0.0.1:8000/convert/path \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/path/to/document.pdf",
    "output_dir": "/output/path",
    "tier": "advanced",
    "pages": "1-10"
  }'

# Batch folder conversion
curl -X POST http://127.0.0.1:8000/convert/folder \
  -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/docs/"}'

# File upload
curl -X POST http://127.0.0.1:8000/convert/upload \
  -F "file=@/path/to/document.docx"
```

## Tests

Run the regression suite in the project's environment:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

```cmd
venv\Scripts\python.exe -m unittest discover -s tests -v
```

The suite covers API errors and fallback, output collisions and rollback,
model completeness and backend selection, and isolated startup-script
fixtures. Shell tests require Bash (Git Bash works on Windows); they use
mock package managers and systemd commands.

## Project Structure

```
doc2md-service/
├── start.sh               # One-click start (Linux / macOS)
├── start.bat              # One-click start (Windows, background)
├── start.vbs              # Silent launcher (Windows, no window at all)
├── stop.bat               # Stop the background service (Windows)
├── update.sh              # Download / update MinerU models (Linux / macOS)
├── update.bat             # Download / update MinerU models (Windows)
├── src/                   # Application source
│   ├── converter_service.py   # FastAPI routing
│   ├── launcher.py            # Background launcher
│   ├── model_manager.py       # Local model/config management
│   └── engines/               # Pluggable converter engines
│       ├── base.py
│       ├── registry.py
│       ├── mineru.py
│       └── markitdown.py
├── scripts/               # Autostart helpers
│   ├── docs2md.service        # systemd user service template
│   ├── install-autostart.sh   # Install autostart (Linux / systemd)
│   ├── install-autostart.bat  # Install autostart (Windows)
│   ├── update.py              # Model update logic
│   └── update_models.py       # Backwards-compatible wrapper
├── config/                # Runtime configuration files
│   └── mineru.yaml            # MinerU 4.x config (auto-generated)
├── mineru_models/         # Model weights (not committed)
│   ├── MinerU-4_models_torch/ # Small models (Torch)
│   ├── MinerU-4_models_onnx/  # Small models (ONNX)
│   └── MinerU2.5-Pro-2605-1.2B-GGUF/ # VLM (llama.cpp)
├── requirements.txt       # Python dependencies
├── README.md              # This file
├── .gitignore             # Git ignore rules
├── LICENSE                # MIT License
└── .claude/
    └── skills/
        └── docs2md/       # Agent skill for AI-powered conversion
            └── SKILL.md
```

## Agent Skill

This repository includes a [Claude Code skill](https://agentskills.io) at
`.claude/skills/docs2md/SKILL.md`. When this project is open in Claude Code or
another Agent Skills-compatible agent, the agent can:

- Start the service automatically (if not running)
- Convert documents by calling the local API
- Batch-process entire folders

The skill works by sending HTTP requests to `http://127.0.0.1:8000` — just
make sure the service is running first with `./start.sh` or `start.bat`.

### Global Installation

To use the `docs2md` skill from **any project** (not just this repo),
install it globally:

```bash
# From the project root:
cp -r .claude/skills/docs2md ~/.agents/skills/docs2md

# Or create a symlink:
ln -s "$(pwd)/.claude/skills/docs2md" ~/.agents/skills/docs2md
```

After installing, set the `DOCS2MD_HOME` environment variable so the skill
can find the project from any directory:

```bash
# Add to ~/.bashrc or ~/.zshrc:
export DOCS2MD_HOME=/path/to/doc2md-service
```

Now any Claude Code session (in any project) can convert documents via this
service:

## Updating

To follow upstream MinerU / MarkItDown releases, pull the latest version of
this repo and restart the service — the start scripts detect the changed
`requirements.txt` and reinstall dependencies automatically:

```bash
git pull
./start.sh                      # reinstalls deps when requirements.txt changed
./update.sh --tier standard     # refresh model weights when MinerU changed its models
```

You can also upgrade dependencies manually inside the venv:

```bash
venv/bin/pip install -U "mineru>=4.0,<5" "markitdown[all]>=0.1.8,<0.2"
```

## Troubleshooting

### "MinerU models for tier ... are missing"

Ensure you have completed Step 2 (Download MinerU models) and the
`mineru_models/` directory contains the small (and, for standard/advanced,
VLM) weights. `GET /health` reports per-tier readiness.

### "mineru-kit not found"

Install the MinerU 4.x CLI into the project venv:

```bash
venv/bin/pip install "mineru>=4.0,<5"
```

### "CUDA out of memory" or GPU errors

Use a lower tier or install a CPU-only stack:

```bash
# Request a cheaper tier per call
#   {"tier": "flash"}  or  {"tier": "basic"}
```

GPU users can install `mineru[full]` for Torch acceleration.

### Conversions time out on long documents

The MinerU CLI timeout defaults to 1800 seconds. Override it with the
`DOCS2MD_MINERU_TIMEOUT` environment variable:

```bash
export DOCS2MD_MINERU_TIMEOUT=3600
```

### Windows path too long errors

The service automatically handles long filenames by copying them to a
short temporary name. If you still encounter path issues, ensure your
project is located in a short path (e.g., `D:\doc2md\` rather than a
deeply nested directory).

### HuggingFace inaccessible (network restricted regions)

Use ModelScope as the model source for the initial download:

```bash
./update.sh modelscope --tier standard
```

## Security Note

This service is designed for **local use only**. Do not expose it directly
to the public internet without adding authentication and authorization.

## License

MIT License — see [LICENSE](LICENSE).
