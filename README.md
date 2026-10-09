# Document to Markdown Converter Service

A local HTTP microservice that converts PDF, DOCX, PPTX, XLSX, HTML, CSV, images, and other formats to Markdown. Powered by **[MinerU](https://github.com/opendatalab/MinerU)** (primary) and **[MarkItDown](https://github.com/microsoft/markitdown)** (fallback).

Locked upstream versions: **MinerU 4.0.5** and **MarkItDown 0.1.8**.

> **Breaking (4.0.0):** MinerU 3.x request fields (`backend`, `effort`, `method`, `lang`, `formula_enable`, `table_enable`, `server_url`, `start_page`, `end_page`) were replaced by `tier` / `pages` / `remote`. Re-download models with `./update.sh` — 3.x model trees are not recognized.

## Features

- **High-quality document conversion** — MinerU 4.x quality tiers handle complex layouts, math formulas, tables, and multi-column papers.
- **Quality tiers** — `flash` (fast preview / native docs), `basic` (OCR / formula / table), `standard` (complex layout, default), `advanced` (hard documents, higher compute). PDF and images support all four tiers; Office / HTML / CSV / EPUB and similar formats parse as whole-document `flash`.
- **Native multi-format MinerU** — PDF, images, DOCX/PPTX/XLSX, HTML, CSV/TSV, RTF, OpenDocument, EPUB, OFD route to MinerU; MarkItDown covers the rest and acts as automatic fallback.
- **Remote inference** — pass `remote: true` to offload MinerU inference (`--remote`).
- **Automatic fallback** — Falls back to MarkItDown if MinerU conversion fails (`fallback: true`, `fallback_from: "mineru"`). PDF page selections require MinerU; failures return an error instead of falling back to whole-document conversion.
- **Batch processing** — Convert an entire folder of documents at once.
- **Page ranges** — Re-convert just a slice of a long paper with the 4.0 page-spec syntax (`pages: "1-5,8"` or `"r3-r1"`).
- **Predictable output layout** — Every file is saved to `<output_dir>/<filename>.docs2md/<stem>.md` with extracted images in `<output_dir>/<filename>.docs2md/images/`. The complete source filename separates outputs such as `report.pdf` and `report.docx`; very long directory names use a shortened prefix and digest.
- **Lightweight API responses** — The API returns status, engine, and the saved file path; it never returns the full Markdown content in the JSON body.
- **Three API modes** — Convert by local file path, file upload, or folder path.
- **One-click start** — `start.sh` (Linux/macOS) and `start.bat` (Windows) handle venv creation, dependency install, and service launch. Dependencies are installed with package hashes and refreshed when a dependency lock or its editable input changes.
- **Swagger UI** — Interactive API docs at `/docs`.
- **Health check** — `GET /health` reports service status, per-tier model readiness, GPU status, and conversion resource usage.
- **Bounded runtime work** — Limit active conversions, pending requests, conversion duration, numerical-library threads, document size, and folder batch size.
- **Agent skill** — Includes an Agent Skills-compatible skill (`skills/docs2md/`) so AI agents can call the service to convert documents.

## Requirements

| Requirement | Minimum |
|---|---|
| Operating System | Windows / Linux (Ubuntu 22.04+ or glibc 2.34+); macOS 14+ is not covered by CI |
| Python | 3.12 (tested on Windows and Linux) |
| RAM | 16 GB (32 GB recommended) |
| Disk (free space) | 20 GB (SSD recommended) |
| GPU VRAM (optional) | 4 GB for GPU acceleration |
| MinerU | `4.0.5` (installed automatically) |
| MarkItDown | `0.1.8` (installed automatically) |

GPU users who want Torch acceleration can install the full extra themselves: `pip install 'mineru[full]'` (the base `mineru` package is CPU/ONNX friendly). GPU extras are outside the tested CPU lock profile. The portable lock includes upstream platform markers, including macOS ARM dependencies. MinerU advertises Python `>=3.10,<3.15`, but native wheel availability limits installation on some Python/platform combinations; use Python 3.12 for the tested setup.

## Quick Start

### 1. Clone

```bash
git clone https://github.com/yangzhexian/doc2md-service.git
cd doc2md-service
```

### 2. Download MinerU models

Use the included updater to download the model weights into the project-local `mineru_models/` directory:

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

The updater calls `mineru-kit models download --tier <tier>` and `mineru-kit models verify --tier <tier>`, then writes `config/mineru.yaml` so the service uses the project-local weights. On Windows, the generated config selects the llama.cpp backend and the updater downloads its GGUF model and projector. MinerU 4.0.5's LMDeploy backend fails on some Windows installations when it loads the vision model. To choose a different backend, set `DOCS2MD_MINERU_VLM_ENGINE` to `auto`, `lmdeploy`, `vllm`, `mlx`, or `llama-cpp` before starting or updating the service.

> **Note:** MinerU 4.x model packages differ from 3.x. Re-run `./update.sh` after upgrading — old `mineru_models/` trees are not recognized.

### 3. Start

```bash
./start.sh      # Linux / macOS
start.bat       # Windows (background, close CMD safely)
start.vbs       # Windows (completely silent, no window)
```

The script handles everything automatically — virtual environment, dependencies, and service launch. Pass a port number to change from the default 8000: `./start.sh 9090`.  On Windows, use `stop.bat` to stop the background service.

On Linux/macOS, `./start.sh --port 9090 --host 0.0.0.0` selects the listening address. Use `./start.sh --setup-only` to prepare dependencies without starting a server.

Open **[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)** for the interactive Swagger UI.

### 4. (Optional) Autostart on Login / Boot

The service can be configured to start automatically when you log in, so you never need to run `start.sh` / `start.bat` manually.

#### Linux (systemd user service)

```bash
./scripts/install-autostart.sh        # default port 8000
./scripts/install-autostart.sh 9090   # custom port
```

This creates a **systemd user service** that starts on login and restarts automatically on failure. Manage it with:

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

The installer waits for dependency setup to finish before enabling the service. The systemd installer requires Linux with systemd; on macOS, use `start.sh` directly or configure a launchd job separately.

#### Windows (Startup folder)

```cmd
scripts\install-autostart.bat         # default port 8000
scripts\install-autostart.bat 9090    # custom port
```

This creates a small batch file in your **Windows Startup folder** (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`) that runs `start.vbs` silently on login — no console window appears.

To remove autostart on Windows, delete `docs2md.bat` from your Startup folder.

## Runtime Resource Controls

All conversion endpoints share a bounded FIFO queue. By default, one document converts at a time and up to four requests wait for a slot; requests that would overwrite the same document output are serialized. Each conversion runs in a separate process, including its engine fallback. The conversion deadline covers worker startup, engine conversion, and fallback. After the worker and its descendant processes stop, the parent publishes the final Markdown and images while still holding the slot. Final publication is outside the worker deadline so image replacement and rollback can complete without interruption.

A full queue or an expired queue wait returns HTTP 429 with `Retry-After`; an expired conversion deadline returns HTTP 504 for single-file requests. Folder requests stop on overload and return HTTP 429; other per-file failures, including deadlines, appear in the batch results with `status_code`. Outputs already written before a later batch failure remain available. Clients may retry rejected work after the indicated delay.

Input limits apply to local paths, uploads, and folder batches. Oversized files return HTTP 413. Upload requests are bounded before multipart parsing and checked again while copying the document; multipart headers and form fields receive an additional 1 MiB allowance above the file limit. At most `max_concurrent + max_pending` upload requests may be in progress, including parsing and conversion; the default is five, with excess uploads returning HTTP 429 and `Retry-After: 1`. JSON bodies for path and folder requests have a fixed 64 KiB limit.

| Environment variable | Default | Effect |
|---|---|---|
| `DOCS2MD_MAX_CONCURRENT_CONVERSIONS` | `1` | Maximum active conversion workers |
| `DOCS2MD_MAX_PENDING_CONVERSIONS` | `4` | Maximum queued conversions; `0` disables waiting |
| `DOCS2MD_QUEUE_TIMEOUT_SECONDS` | `30` | Maximum time waiting for a conversion slot |
| `DOCS2MD_CONVERSION_TIMEOUT_SECONDS` | `1800` | Deadline for one worker, including fallback |
| `DOCS2MD_WORKER_THREADS` | `2` | Thread setting for each numerical-library pool in a worker |
| `DOCS2MD_MAX_FILE_BYTES` | `104857600` (100 MiB) | Maximum size of one input document |
| `DOCS2MD_MAX_BATCH_FILES` | `100` | Maximum supported files in one folder request |
| `DOCS2MD_MAX_FOLDER_ENTRIES` | `10000` | Maximum directory entries examined during folder preflight |

Folder conversion scans only the immediate directory. It validates the complete bounded batch before starting conversions, so a batch exceeding its file, entry, or document-size limit returns HTTP 413 without converting a partial batch. Subdirectories and unsupported files count toward the scan limit; only supported files count toward the batch limit.

`DOCS2MD_WORKER_THREADS` applies to OpenMP/BLAS pools, ONNX and PyTorch intra-op pools, and llama.cpp generation threads, including the separate MinerU CLI process. ONNX and PyTorch inter-op pools use one thread.

Configure these variables before starting the service; invalid values stop startup with an actionable configuration error. Integer limits must be positive, except the pending limit may be zero. Timeouts must be finite positive numbers.

```bash
export DOCS2MD_MAX_CONCURRENT_CONVERSIONS=1
export DOCS2MD_WORKER_THREADS=2
export DOCS2MD_CONVERSION_TIMEOUT_SECONDS=1800
./start.sh
```

Use one Uvicorn server process so every request shares the same budget. These settings bound active work and common numerical-library thread pools; they do not impose a hard CPU-percentage, RAM, or GPU-memory cap. Choose the quality tier and concurrency according to available memory.

## How MinerU Models Are Configured

This project stores MinerU model weights locally in the `mineru_models/` directory so that no network access is needed at runtime.

On startup, the service writes `config/mineru.yaml`:

```yaml
model:
  source: local
  base_dir: <project>/mineru_models
  small_backend: auto
  vlm:
    engine: llama-cpp  # Windows default; auto on other platforms
```

and sets `MINERU_CONFIG` to point at it so the MinerU 4.x CLI uses the project-local models. (MinerU 4.x no longer reads `config/mineru.json`.)

This means the service is **self-configuring** — you just need to ensure `mineru_models/` exists with the downloaded model files.

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

PDF and images support every tier. Office / OpenDocument / RTF / EPUB / OFD / HTML / CSV-TSV are whole-document `flash` parses regardless of the requested tier. Pure text (`.txt` / `.md`) is not handled by MinerU.

For academic papers (math-heavy, dense tables, complex two-column layouts), download the standard package (`./update.sh`) and keep the default `tier=standard`. Use `tier=advanced` only when `standard` is not enough.

### Page specs

| Spec | Meaning |
|---|---|
| `all` | whole document (default) |
| `1-5` | pages 1 through 5 (1-based, inclusive) |
| `1-5,8` | pages 1–5 plus page 8 |
| `r3-r1` | 3rd-from-end through last page |

Page subsets are supported only for PDF inputs with MinerU. Other formats and explicit MarkItDown requests require `pages: "all"`. If MinerU fails for a page subset, the API returns an error and skips whole-document fallback.

## API Reference

### GET /health

Returns service status, registered engines, quality tiers, per-tier model readiness, and a snapshot of runtime limits and conversion counters.

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
  "cuda_available": true,
  "resources": {
    "limits": {
      "max_concurrent": 1,
      "max_pending": 4,
      "queue_timeout_seconds": 30.0,
      "conversion_timeout_seconds": 1800.0,
      "worker_threads": 2,
      "max_file_bytes": 104857600,
      "max_batch_files": 100,
      "max_folder_entries": 10000,
      "max_json_body_bytes": 65536,
      "max_upload_body_bytes": 105906176,
      "max_uploads": 5
    },
    "active": 0,
    "pending": 0,
    "completed": 0,
    "failed": 0,
    "rejected": 0,
    "conversion_timeouts": 0,
    "queue_timeouts": 0
  }
}
```

- `models_ready` — whether the selected backend has its complete required model files and runtime dependencies (`flash` is always ready).
- `resources` — effective limits and counters for this server process; counters reset on restart.

---

### POST /convert/path

Convert a file by its local absolute path. Results are saved to `<output_dir>/<filename>.docs2md/<stem>.md` by default, with images (if any) in `<output_dir>/<filename>.docs2md/images/`.

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

The Markdown content is **not** returned in the response. Read it from `output_path`.

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

The default `output_dir` for uploads is the project `output/` directory, or the value of the `DOCS2MD_UPLOAD_OUTPUT_DIR` environment variable. This prevents results from being lost when the upload temp directory is cleaned up.

**Response:** Same as `/convert/path`.

---

### POST /convert/folder

Batch-convert supported files in the immediate folder. Results are saved to `<output_dir>/<filename>.docs2md/<stem>.md` by default. Accepted extensions are the union of every registered engine. File, batch, and scan limits are checked before converting any file. Conversion errors are returned per file with `status: "error"`, `detail`, and `status_code` when available; queue overload stops the batch with HTTP 429.

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

Invalid `tier` / `pages` values also return 400 (or 422 for JSON body validation) with an actionable message.

**409 — Output not writable:**
```json
{ "detail": "Unable to write conversion output '...': ... Choose a writable output_dir ..." }
```

**413 — Input or batch too large:**
```json
{ "detail": "File exceeds 104857600 bytes: /path/to/document.pdf" }
```

Reduce the document size or batch size, or configure a larger resource limit before starting the service.

**429 — Resource queue busy:**
```json
{ "detail": "Conversion queue is full; retry later" }
```

A full conversion queue or an expired queue wait returns `Retry-After` in seconds. Retry after that delay.

**504 — Conversion deadline exceeded:**
```json
{ "detail": "Conversion exceeded 1800 seconds; its worker process tree was stopped" }
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

CI runs on **Ubuntu 24.04 and Windows Server 2022, Python 3.12** for every push and pull request. It verifies lock consistency, installs with package hashes, runs `pip check`, checks Bash syntax, and executes the full suite. Per-platform real-document JSON reports are retained as workflow artifacts for 14 days. OCR is disabled in this CPU CI job.

Install the complete runtime + test lock, then run the regression suite:

```bash
./venv/bin/python -m pip install --require-hashes -r requirements-test.txt
./venv/bin/python -m unittest discover -s tests -v
```

```cmd
venv\Scripts\python.exe -m pip install --require-hashes -r requirements-test.txt
venv\Scripts\python.exe -m unittest discover -s tests -v
```

The suite covers API errors and fallback, output collisions and rollback, model completeness and backend selection, dependency refresh, isolated startup scripts, admission limits, worker deadlines, process cleanup, upload bounds, and batch preflight. Shell tests require Bash (Git Bash works on Windows); only the startup tests mock package managers and systemd commands.

The checked-in [real-document corpus](tests/fixtures/real_documents/README.md) contains self-authored PDF, DOCX, PPTX, XLSX, CSV, HTML, and scanned PNG files. The mandatory regressions run 14 actual conversions through MinerU `flash` and MarkItDown, checking content, tables, PDF page selections, links, exported images, and unchanged input hashes. They isolate configuration and use no downloaded models or live service. One PNG OCR regression is opt-in with preinstalled local models:

```bash
DOCS2MD_RUN_OCR=1 DOCS2MD_OCR_MODEL_DIR=/absolute/path/to/models \
  ./venv/bin/python -m unittest discover -s tests -p test_real_documents.py -v
```

Set `DOCS2MD_REAL_REGRESSION_REPORT` to a JSON file path to retain the local case results. See the corpus README for Windows environment settings, Torch backend selection, and deterministic fixture regeneration.

## Dependency Locks

`requirements.in` pins direct runtime dependencies; `requirements.txt` locks their complete dependency graph with platform markers and SHA256 hashes. `requirements-test.in` adds test dependencies and constrains them to the runtime lock; `requirements-test.txt` is a standalone, complete test installation. Both launchers use the runtime lock with `--require-hashes`. GPU extras and model weights are managed separately from this CPU dependency profile.

Edit the `.in` files, then regenerate both locks in an isolated tooling environment with the pinned resolver:

```bash
python -m venv tmp/lock-tools
# Linux/macOS (Windows: tmp\lock-tools\Scripts\python.exe)
tmp/lock-tools/bin/python -m pip install uv==0.9.30
tmp/lock-tools/bin/python scripts/lock_dependencies.py
python scripts/lock_dependencies.py --check
```

Regeneration retains compatible versions from existing locks. To deliberately upgrade a transitive package, use the tooling environment to update the runtime lock first:

```bash
tmp/lock-tools/bin/python -m uv pip compile requirements.in \
  --universal --python-version 3.10 --generate-hashes \
  --default-index https://pypi.org/simple --no-config \
  --upgrade-package PACKAGE --output-file requirements.txt
tmp/lock-tools/bin/python scripts/lock_dependencies.py
```

The regeneration script synchronizes the test lock and manifest. Commit both inputs, both locks, and `requirements.lock.json` together, and validate the clean installation and real-document suite. `--check` works offline and detects input/output changes without treating Windows CRLF as a version change.

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
│   ├── resource_control.py    # Queue, worker deadlines, and process cleanup
│   ├── resource_limits.py     # File, upload, and folder limits
│   ├── conversion_worker.py   # Isolated engine and fallback execution
│   ├── launcher.py            # Background launcher
│   ├── model_manager.py       # Local model/config management
│   └── engines/               # Pluggable converter engines
│       ├── base.py
│       ├── registry.py
│       ├── mineru.py
│       └── markitdown.py
├── .github/workflows/ci.yml # Windows/Linux regression CI
├── scripts/               # Setup, locking, and autostart helpers
│   ├── docs2md.service        # systemd user service template
│   ├── install-autostart.sh   # Install autostart (Linux / systemd)
│   ├── install-autostart.bat  # Install autostart (Windows)
│   ├── update.py              # Model update logic
│   ├── lock_dependencies.py   # Regenerate/check portable locks
│   └── requirements_hash.py   # Shared dependency installation marker
├── config/                # Runtime configuration files
│   └── mineru.yaml            # MinerU 4.x config (auto-generated)
├── mineru_models/         # Model weights (not committed)
│   ├── MinerU-4_models_torch/ # Small models (Torch)
│   ├── MinerU-4_models_onnx/  # Small models (ONNX)
│   └── MinerU2.5-Pro-2605-1.2B-GGUF/ # VLM (llama.cpp)
├── requirements.in        # Direct runtime dependency pins
├── requirements.txt       # Runtime dependency graph + hashes
├── requirements-test.in   # Test inputs + runtime constraints
├── requirements-test.txt  # Full runtime/test dependency lock
├── requirements.lock.json # Lock provenance and consistency hashes
├── tests/                 # Unit and real document regressions
├── README.md              # This file
├── .gitignore             # Git ignore rules
├── LICENSE                # MIT License
└── skills/
    └── docs2md/           # Agent skill distributed with the service
        └── SKILL.md
```

## Agent Skill

The service ships an [Agent Skills-compatible skill](https://agentskills.io) at `skills/docs2md/SKILL.md`, outside the ignored `.claude/` and `.mimocode/` directories. Load this directory into your agent to start the service, convert individual documents, or batch-process folders through `http://127.0.0.1:8000`.

### Optional Global Installation

To make the skill available across projects, copy the tracked skill directory into your agent's global skills directory:

```bash
# From the project root:
cp -r skills/docs2md ~/.agents/skills/docs2md
```

On Windows, the equivalent destination is `%USERPROFILE%\.agents\skills\docs2md`. Set `DOCS2MD_HOME` to the service checkout so the copied skill can locate its launcher:

```bash
# Add to ~/.bashrc or ~/.zshrc:
export DOCS2MD_HOME=/path/to/doc2md-service
```

```powershell
# Current Windows session:
$env:DOCS2MD_HOME = 'D:\github-repo\docs2md'
```

Keep the global copy updated when the service skill changes. Local agent configuration in `.claude/` and `.mimocode/` is ignored by Git; the shared skill remains tracked under `skills/docs2md/`.

## Updating

To follow upstream MinerU / MarkItDown releases, pull the latest version of this repo and restart the service — the start scripts detect the changed dependency locks and reinstall the pinned dependencies automatically:

```bash
git pull
./start.sh                      # reinstalls deps when requirements.txt changed
./update.sh --tier standard     # refresh model weights when MinerU changed its models
```

To change dependency versions, follow [Dependency Locks](#dependency-locks) and run the regression suite before committing the new locks.

## Troubleshooting

### "MinerU models for tier ... are missing"

Ensure you have completed Step 2 (Download MinerU models) and the `mineru_models/` directory contains the small (and, for standard/advanced, VLM) weights. `GET /health` reports per-tier readiness.

### "mineru-kit not found"

Restore the locked dependencies into the project venv:

```bash
venv/bin/python -m pip install --require-hashes -r requirements.txt
```

### "CUDA out of memory" or GPU errors

Use a lower tier or install a CPU-only stack:

```bash
# Request a cheaper tier per call
#   {"tier": "flash"}  or  {"tier": "basic"}
```

GPU users can install `mineru[full]` for Torch acceleration.

### Conversions time out on long documents

The service worker deadline defaults to 1800 seconds, including engine startup and fallback. `DOCS2MD_CONVERSION_TIMEOUT_SECONDS` configures that deadline. MinerU also has its own CLI timeout, configured by `DOCS2MD_MINERU_TIMEOUT`; the service deadline still applies if the CLI timeout is longer. To allow a longer conversion, set both before restarting:

```bash
export DOCS2MD_CONVERSION_TIMEOUT_SECONDS=3600
export DOCS2MD_MINERU_TIMEOUT=3600
```

### Windows path too long errors

The service automatically handles long filenames by copying them to a short temporary name. If you still encounter path issues, ensure your project is located in a short path (e.g., `D:\doc2md\` rather than a deeply nested directory).

### HuggingFace inaccessible (network restricted regions)

Use ModelScope as the model source for the initial download:

```bash
./update.sh modelscope --tier standard
```

## Security Note

This service is designed for **local use only**. Do not expose it directly to the public internet without adding authentication and authorization.

## License

MIT License — see [LICENSE](LICENSE).
