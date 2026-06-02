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
- **Custom output directory** — Results are saved to `<source>/docs2md/` by
  default; override with any path.
- **Three API modes** — Convert by local file path, file upload, or folder path.
- **One-click start** — `start.sh` (Linux/macOS) and `start.bat` (Windows)
  handle venv creation, dependency install, and service launch.
- **Swagger UI** — Interactive API docs at `/docs`.
- **Health check** — `GET /health` reports service status and GPU availability.
- **Agent skill** — Includes a Claude Code skill (`.claude/skills/doc2md/`)
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

### 1. Clone the repository

```bash
git clone https://github.com/yangzhexian/doc2md-service.git
cd doc2md-service
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Linux / macOS
source venv/bin/activate

# Windows
venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

> **Note:** `mineru[all]` includes PyTorch and all core features. The first
> installation may take several minutes.

### 4. Download MinerU models

MinerU requires model weights (~1.2 GB) for PDF parsing. These are stored
in the `mineru_models/` directory within the project.

```bash
# Download models from HuggingFace (default)
mineru-models-download

# If HuggingFace is inaccessible from your region, use ModelScope instead:
# (Linux / macOS)
export MINERU_MODEL_SOURCE=modelscope
# (Windows CMD)
set MINERU_MODEL_SOURCE=modelscope
# (Windows PowerShell)
$env:MINERU_MODEL_SOURCE="modelscope"

mineru-models-download
```

After downloading, copy the models into the project directory:

```bash
# Locate the downloaded model directory (printed by mineru-models-download)
# or find it at:
#   Linux/macOS: ~/.cache/huggingface/hub/models--opendatalab--PDF-Extract-Kit-1.0/snapshots/<hash>/
#   Windows:     %USERPROFILE%\.cache\huggingface\hub\models--opendatalab--PDF-Extract-Kit-1.0\snapshots\<hash>\
#
#   ModelScope:
#   Linux/macOS: ~/.cache/modelscope/hub/models/opendatalab/PDF-Extract-Kit-1.0/
#   Windows:     %USERPROFILE%\.cache\modelscope\hub\models\opendatalab\PDF-Extract-Kit-1.0\

# Copy the model files into the project (example path — adjust to your cache location):
cp -rL ~/.cache/huggingface/hub/models--opendatalab--PDF-Extract-Kit-1.0/snapshots/*/models mineru_models/
```

> **Alternatively**, set the `MINERU_MODEL_SOURCE` environment variable to
> `huggingface` or `modelscope` and skip the manual copy step. However, the
> service will need network access on first run to download models.

### 5. Start the service

**One-click (recommended):**

```bash
# Linux / macOS
./start.sh

# Windows
start.bat
```

Or manually:

```bash
uvicorn converter_service:app --host 127.0.0.1 --port 8000
```

By default the service listens on `127.0.0.1:8000`. Pass a port number to
the start script to change it (`./start.sh 9090`).

Once started, open your browser to:

- **Swagger UI:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Health check:** [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

### 6. Test the conversion

```bash
# Health check
curl http://127.0.0.1:8000/health

# Convert a PDF by local path
curl -X POST http://127.0.0.1:8000/convert/path \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/absolute/path/to/document.pdf"}'

# Convert a file by upload
curl -X POST http://127.0.0.1:8000/convert/upload \
  -F "file=@/path/to/document.docx"
```

## How MinerU Models Are Configured

This project stores MinerU model weights locally in the `mineru_models/`
directory so that no network access is needed at runtime.

On startup, `converter_service.py` automatically:

1. Sets `MINERU_MODEL_SOURCE=local` to use local models.
2. Writes `mineru.json` and `magic-pdf.json` to your user home directory
   with the correct absolute path to `mineru_models/`.
3. Sets `MINERU_TOOLS_CONFIG_JSON` to point to the project's config template.

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
  "mineru_available": true,
  "gpu_available": true
}
```

---

### POST /convert/path

Convert a file by its local absolute path. Results are saved to
`<source_parent>/docs2md/<stem>.md` by default.

**Request body (JSON):**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_path` | string | yes | — | Absolute path to the file |
| `output_dir` | string | no | `<parent>/docs2md/` | Directory for output `.md` files |
| `use_mineru_for_pdf` | bool | no | `true` | Use MinerU for PDF files |
| `mineru_method` | string | no | `"ocr"` | Parse method: `ocr`, `txt`, or `auto` |
| `mineru_lang` | string | no | `null` | OCR language: `en`, `ch`, etc. (`null` = auto) |

**Response:**
```json
{
  "status": "success",
  "markdown": "# Document Title\n\nContent...",
  "engine": "mineru",
  "output_path": "/path/to/source/docs2md/document.md",
  "detail": null
}
```

---

### POST /convert/upload

Upload a file for conversion.

**Request (multipart/form-data):**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `file` | file | yes | — | File to convert |
| `output_dir` | string | no | `null` | Directory for output `.md` file |
| `use_mineru_for_pdf` | bool | no | `true` | Use MinerU for PDF files |
| `mineru_method` | string | no | `"ocr"` | Parse method |
| `mineru_lang` | string | no | `null` | OCR language |

**Response:** Same as `/convert/path`.

---

### POST /convert/folder

Batch-convert all supported files in a folder. Results are saved to
`<folder>/docs2md/<relative_path>/<stem>.md` by default.

**Request body (JSON):**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `folder_path` | string | yes | — | Absolute path to the folder |
| `output_dir` | string | no | `<folder>/docs2md/` | Directory for output `.md` files |
| `recursive` | bool | no | `true` | Recurse into subdirectories |
| `use_mineru_for_pdf` | bool | no | `true` | Use MinerU for PDF files |
| `mineru_method` | string | no | `"ocr"` | Parse method |
| `mineru_lang` | string | no | `null` | OCR language |

**Response:**
```json
{
  "status": "success",
  "total": 5,
  "succeeded": 5,
  "failed": 0,
  "results": [
    {
      "file_path": "/path/to/docs/paper.pdf",
      "stem": "paper",
      "engine": "mineru",
      "output_path": "/path/to/docs/docs2md/paper.md",
      "status": "success",
      "error": null
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
        "use_mineru_for_pdf": True,
        "output_dir": "/custom/output/dir",  # optional
    },
)
data = resp.json()
print(f"Engine: {data['engine']}, Output: {data['output_path']}")
print(data["markdown"][:200])

# Batch-convert a folder
resp = requests.post(
    "http://127.0.0.1:8000/convert/folder",
    json={
        "folder_path": "/absolute/path/to/docs/",
        "recursive": True,
    },
)
for item in resp.json()["results"]:
    print(f"{item['stem']}: {item['status']} ({item['engine']})")

# Convert by file upload
with open("/path/to/document.docx", "rb") as f:
    resp = requests.post(
        "http://127.0.0.1:8000/convert/upload",
        files={"file": f},
        data={"use_mineru_for_pdf": "false"},
    )
print(resp.json()["markdown"])
```

### curl

```bash
# Single file
curl -X POST http://127.0.0.1:8000/convert/path \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/path/to/document.pdf"}'

# Single file with custom output directory
curl -X POST http://127.0.0.1:8000/convert/path \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/path/to/document.pdf", "output_dir": "/output/path"}'

# Batch folder conversion
curl -X POST http://127.0.0.1:8000/convert/folder \
  -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/docs/", "recursive": true}'

# File upload
curl -X POST http://127.0.0.1:8000/convert/upload \
  -F "file=@/path/to/document.docx"
```

## Project Structure

```
doc2md-service/
├── converter_service.py   # Main FastAPI application
├── start.sh               # One-click start script (Linux / macOS)
├── start.bat              # One-click start script (Windows)
├── mineru.json            # MinerU config template
├── mineru_models/         # Model weights (~1.2 GB, not committed)
│   └── models/            #   Downloaded separately
├── requirements.txt       # Python dependencies
├── README.md              # This file
├── .gitignore             # Git ignore rules
├── LICENSE                # MIT License
└── .claude/
    └── skills/
        └── doc2md/        # Agent skill for AI-powered conversion
            └── SKILL.md
```

## Agent Skill

This repository includes a [Claude Code skill](https://agentskills.io) at
`.claude/skills/doc2md/SKILL.md`. When this project is open in Claude Code or
another Agent Skills-compatible agent, the agent can:

- Start the service automatically (if not running)
- Convert documents by calling the local API
- Batch-process entire folders

The skill works by sending HTTP requests to `http://127.0.0.1:8000` — just
make sure the service is running first with `./start.sh` or `start.bat`.

## Troubleshooting

### "MinerU models directory not found"

Ensure you have completed Step 4 (Download MinerU models) and the
`mineru_models/models/` directory exists in the project root.

### "CUDA out of memory" or GPU errors

Set the device to CPU:

```bash
export MINERU_DEVICE_MODE=cpu
python converter_service.py
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
Step 4.

## Security Note

This service is designed for **local use only**. Do not expose it directly
to the public internet without adding authentication and authorization.

## License

MIT License — see [LICENSE](LICENSE).
