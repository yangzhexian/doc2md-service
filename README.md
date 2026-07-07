# Document to Markdown Converter Service

A local HTTP microservice that converts PDF, DOCX, PPTX, XLSX, HTML, CSV,
images, and other formats to Markdown. Powered by
**[MinerU](https://github.com/opendatalab/MinerU)** (PDF) and
**[MarkItDown](https://github.com/microsoft/markitdown)** (all other formats).

## Features

- **High-quality PDF conversion** — MinerU with GPU-accelerated OCR handles
  complex layouts, math formulas, tables, and multi-column papers.
- **Multi-format support** — DOCX, PPTX, XLSX, HTML, CSV, JSON, XML, images,
  audio, and ZIP via MarkItDown.
- **Automatic fallback** — Falls back to MarkItDown if MinerU PDF conversion fails.
- **Batch processing** — Convert an entire folder of documents at once.
- **Predictable output layout** — Every file is saved to
  `<output_dir>/<stem>/<stem>.md` with extracted images in
  `<output_dir>/<stem>/images/`.
- **Lightweight API responses** — The API returns status, engine, and the saved
  file path; it never returns the full Markdown content in the JSON body.
- **Three API modes** — Convert by local file path, file upload, or folder path.
- **One-click start** — `start.sh` (Linux/macOS) and `start.bat` (Windows)
  handle venv creation, dependency install, and service launch.
- **Swagger UI** — Interactive API docs at `/docs`.
- **Health check** — `GET /health` reports service status and GPU availability.
- **Agent skill** — Includes a Claude Code skill (`.claude/skills/docs2md/`)
  so AI agents can call the service to convert documents.

## Requirements

| Requirement | Minimum |
|---|---|
| Operating System | Linux / Windows / macOS 14+ |
| Python | 3.10 – 3.13 |
| RAM | 16 GB (32 GB recommended) |
| Disk (free space) | 20 GB (SSD recommended) |
| GPU VRAM (optional) | 4 GB for GPU acceleration |

## Quick Start

### 1. Clone

```bash
git clone https://github.com/yangzhexian/doc2md-service.git
cd doc2md-service
```

### 2. Download MinerU models (~1.2 GB)

Use the included updater to download the pipeline models and copy them into the
project-local `mineru_models/` directory:

```bash
# Linux / macOS
./update.sh                # auto-select source
./update.sh modelscope     # force ModelScope

# Windows
update.bat                 # auto-select source
update.bat modelscope      # force ModelScope
```

The updater calls `mineru-models-download`, locates the downloaded cache, and
copies the `models/` tree to `mineru_models/`. It then writes the runtime
configuration files in `config/`.

### 3. Start

```bash
./start.sh      # Linux / macOS
start.bat       # Windows (background, close CMD safely)
start.vbs       # Windows (completely silent, no window)
```

The script handles everything automatically — virtual environment, dependencies,
and service launch. Pass a port number to change from the default 8000:
`./start.sh 9090`.  On Windows, use `stop.bat` to stop the background service.

Open **[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)** for the interactive Swagger UI.

### 4. (Optional) Autostart on Login / Boot

The service can be configured to start automatically when you log in,
so you never need to run `start.sh` / `start.bat` manually.

#### Linux / macOS (systemd user service)

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

On startup, `converter_service.py` automatically:

1. Writes `config/mineru.json` (and a legacy `config/magic-pdf.json`) with the
   correct absolute path to `mineru_models/`.
2. Sets `MINERU_TOOLS_CONFIG_JSON` to point to `config/mineru.json` so the
   MinerU CLI uses the project-local models.
3. Sets `MINERU_MODEL_SOURCE=local` when invoking the MinerU CLI.

This means the service is **self-configuring** — you just need to ensure
`mineru_models/` exists with the downloaded model files.

### Model directory structure

```
mineru_models/
└── models/
    ├── Layout/
    │   └── PP-DocLayoutV2/
    ├── MFR/
    │   └── unimernet_hf_small_2503/
    ├── OCR/
    │   └── paddleocr_torch/
    ├── TabCls/
    │   └── paddle_table_cls/
    └── TabRec/
        ├── SlanetPlus/
        └── UnetStructure/
```

## API Reference

### GET /health

Returns service status, MinerU availability, and GPU status.

**Response:**
```json
{
  "status": "ok",
  "engines": ["mineru", "markitdown"],
  "default_engine": "mineru",
  "models_ready": true,
  "cuda_available": true
}
```

---

### POST /convert/path

Convert a file by its local absolute path. Results are saved to
`<output_dir>/<stem>/<stem>.md` by default, with images (if any) in
`<output_dir>/<stem>/images/`.

**Request (`application/json`):**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_path` | string | yes | — | Absolute path to the file |
| `output_dir` | string | no | parent of `file_path` | Base output directory |
| `engine` | string | no | `mineru` | Engine override: `mineru`, `markitdown`, `auto` |
| `method` | string | no | `"auto"` | MinerU parse method: `auto`, `ocr`, `txt` |
| `lang` | string | no | `""` | MinerU language hint: `ch`, `en`, etc. (`""` = auto-detect) |
| `formula_enable` | bool | no | `true` | Enable MinerU formula recognition |
| `table_enable` | bool | no | `true` | Enable MinerU table recognition |

**Response:**
```json
{
  "success": true,
  "engine": "mineru",
  "output_path": "/path/to/output/document/document.md",
  "output_dir": "/path/to/output",
  "images_dir": "/path/to/output/document/images",
  "fallback": false,
  "message": "Saved to /path/to/output/document/document.md"
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
| `engine` | string | no | `mineru` | Engine override: `mineru`, `markitdown`, `auto` |
| `method` | string | no | `"auto"` | MinerU parse method |
| `lang` | string | no | `""` | MinerU language hint |
| `formula_enable` | bool | no | `true` | Enable MinerU formula recognition |
| `table_enable` | bool | no | `true` | Enable MinerU table recognition |

The default `output_dir` for uploads is the project `output/` directory, or the
value of the `DOCS2MD_UPLOAD_OUTPUT_DIR` environment variable. This prevents
results from being lost when the upload temp directory is cleaned up.

**Response:** Same as `/convert/path`.

---

### POST /convert/folder

Batch-convert all supported files in a folder. Results are saved to
`<output_dir>/<stem>/<stem>.md` by default.

**Request (`application/json`):**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `folder_path` | string | yes | — | Absolute path to the folder |
| `output_dir` | string | no | `folder_path` | Base output directory |
| `engine` | string | no | `mineru` | Engine override: `mineru`, `markitdown`, `auto` |
| `method` | string | no | `"auto"` | MinerU parse method |
| `lang` | string | no | `""` | MinerU language hint |
| `formula_enable` | bool | no | `true` | Enable MinerU formula recognition |
| `table_enable` | bool | no | `true` | Enable MinerU table recognition |

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
      "output_path": "/path/to/docs/paper/paper.md",
      "images_dir": "/path/to/docs/paper/images"
    }
  ]
}
```

### Error Responses

**404 — File not found:**
```json
{ "detail": "File not found: /path/to/nonexistent.pdf" }
```

**400 — Conversion error:**
```json
{ "detail": "MinerU error: ... MarkItDown fallback error: ..." }
```

## Supported File Formats

| Category | Extensions | Engine |
|---|---|---|
| PDF | `.pdf` | MinerU → MarkItDown (fallback) |
| Word | `.docx` | MarkItDown |
| PowerPoint | `.pptx` | MarkItDown |
| Excel | `.xlsx` | MarkItDown |
| Web | `.html`, `.htm` | MarkItDown |
| Data | `.csv`, `.json`, `.xml` | MarkItDown |
| Images | `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.webp` | MarkItDown |
| Audio | `.mp3`, `.wav`, `.ogg`, `.wma`, `.m4a`, `.flac` | MarkItDown |
| Archives | `.zip` | MarkItDown |

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
        "engine": "mineru",                   # optional
    },
)
data = resp.json()
print(f"Engine: {data['engine']}, Output: {data['output_path']}")
with open(data["output_path"], "r", encoding="utf-8") as f:
    print(f.read()[:200])

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

# Single file with custom output directory and engine override
curl -X POST http://127.0.0.1:8000/convert/path \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/path/to/document.pdf",
    "output_dir": "/output/path",
    "engine": "mineru"
  }'

# Batch folder conversion
curl -X POST http://127.0.0.1:8000/convert/folder \
  -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/docs/"}'

# File upload
curl -X POST http://127.0.0.1:8000/convert/upload \
  -F "file=@/path/to/document.docx"
```

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
│   ├── install-autostart.sh   # Install autostart (Linux / macOS)
│   ├── install-autostart.bat  # Install autostart (Windows)
│   ├── update.py              # Model update logic
│   └── update_models.py       # Backwards-compatible wrapper
├── config/                # Runtime configuration files
│   ├── mineru.json            # MinerU 3.4+ config
│   └── magic-pdf.json         # Legacy config
├── mineru_models/         # Model weights (~1.2 GB, not committed)
│   └── models/            #   Downloaded separately
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

## Troubleshooting

### "MinerU models directory not found"

Ensure you have completed Step 2 (Download MinerU models) and the
`mineru_models/models/` directory exists in the project root.

### "CUDA out of memory" or GPU errors

Set the device to CPU:

```bash
export MINERU_DEVICE_MODE=cpu
python src/converter_service.py
```

### Windows path too long errors

The service automatically handles long filenames by copying them to a
short temporary name. If you still encounter path issues, ensure your
project is located in a short path (e.g., `D:\doc2md\` rather than a
deeply nested directory).

### HuggingFace inaccessible (network restricted regions)

Use ModelScope as the model source for the initial download:

```bash
export MINERU_MODEL_SOURCE=modelscope
mineru-models-download
```

After downloading, copy the models to `mineru_models/` as described in
Step 2.

## Security Note

This service is designed for **local use only**. Do not expose it directly
to the public internet without adding authentication and authorization.

## License

MIT License — see [LICENSE](LICENSE).
