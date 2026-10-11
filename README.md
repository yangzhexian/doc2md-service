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
- **Batch processing** — Convert flat or recursive folders with extension filters; preserve partial results and nested output paths.
- **Persistent batch jobs** — Submit a batch, poll progress, cancel future files, and retry failed or interrupted items without rerunning successful files.
- **Page ranges** — Re-convert just a slice of a long paper with the 4.0 page-spec syntax (`pages: "1-5,8"` or `"r3-r1"`).
- **Predictable output layout** — Every file is saved to `<output_dir>/<filename>.docs2md/<stem>.md` with extracted images in `<output_dir>/<filename>.docs2md/images/`. The complete source filename separates outputs such as `report.pdf` and `report.docx`; very long directory names use a shortened prefix and digest.
- **Lightweight API responses** — The API returns status, engine, and the saved file path; it never returns the full Markdown content in the JSON body.
- **API modes** — Convert by local file path, file upload, synchronous folder request, or persistent batch job.
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

A full queue or an expired queue wait returns HTTP 429 with `Retry-After`; an expired conversion deadline returns HTTP 504 for single-file requests. Synchronous folder requests stop on overload and return HTTP 429 with completed `results`, `pending_files`, and `summary` in `detail`; other per-file failures, including deadlines, appear in the batch results with `status_code`. A persistent batch pauses after one item receives three resource-overload responses; delays between attempts honor `Retry-After` and are capped at 60 seconds. Resume with an explicit retry. Outputs already written before a later batch failure remain available. Clients may retry rejected work after the indicated delay.

Input limits apply to local paths, uploads, and folder batches. Oversized files return HTTP 413. Upload requests are bounded before multipart parsing and checked again while copying the document; multipart headers and form fields receive an additional 1 MiB allowance above the file limit. At most `max_concurrent + max_pending` upload requests may be in progress, including parsing and conversion; the default is five, with excess uploads returning HTTP 429 and `Retry-After: 1`. JSON bodies for path, folder, and batch operations have a fixed 64 KiB limit.

| Environment variable | Default | Effect |
|---|---|---|
| `DOCS2MD_MAX_CONCURRENT_CONVERSIONS` | `1` | Maximum active conversion workers |
| `DOCS2MD_MAX_PENDING_CONVERSIONS` | `4` | Maximum queued conversions; `0` disables waiting |
| `DOCS2MD_QUEUE_TIMEOUT_SECONDS` | `30` | Maximum time waiting for a conversion slot |
| `DOCS2MD_CONVERSION_TIMEOUT_SECONDS` | `1800` | Deadline for one worker, including fallback |
| `DOCS2MD_WORKER_THREADS` | `2` | Thread setting for each numerical-library pool in a worker |
| `DOCS2MD_MAX_FILE_BYTES` | `104857600` (100 MiB) | Maximum size of one input document |
| `DOCS2MD_MAX_BATCH_FILES` | `100` | Maximum selected files in one folder request or batch job |
| `DOCS2MD_MAX_FOLDER_ENTRIES` | `10000` | Maximum directory entries examined across one folder scan |
| `DOCS2MD_MAX_BATCH_JOBS` | `5` | Maximum active persistent batch jobs |
| `DOCS2MD_BATCH_HISTORY_LIMIT` | `100` | Maximum retained batch records; must be at least the active-job limit |
| `DOCS2MD_BATCH_STATE_DIR` | `<project>/.docs2md-state/` | Directory for persistent SQLite batch metadata |

Folder conversion scans the immediate directory by default; `recursive: true` includes subdirectories. It validates the complete bounded batch before starting conversions or accepting a persistent job, so a batch exceeding its file, entry, or document-size limit returns HTTP 413 without converting a partial batch. All examined entries count toward the scan limit; only files selected by the extension filter count toward the batch limit. Generated `.docs2md` directories, a separate output subtree inside the input folder, and directory symlinks or Windows junctions are skipped. File symlinks remain inputs under their alias filenames.

`DOCS2MD_WORKER_THREADS` applies to OpenMP/BLAS pools, ONNX and PyTorch intra-op pools, and llama.cpp generation threads, including the separate MinerU CLI process. ONNX and PyTorch inter-op pools use one thread.

Configure these variables before starting the service; invalid values stop startup with an actionable configuration error. Integer limits must be positive, except the pending limit may be zero. Timeouts must be finite positive numbers.

```bash
export DOCS2MD_MAX_CONCURRENT_CONVERSIONS=1
export DOCS2MD_WORKER_THREADS=2
export DOCS2MD_CONVERSION_TIMEOUT_SECONDS=1800
./start.sh
```

Use one Uvicorn server process so every request shares the same budget. Persistent batch jobs use one scheduler that rotates jobs one file at a time and shares the existing conversion controller with synchronous requests. Only one server may own a batch state directory at a time; a second owner fails startup rather than opening the same database concurrently. These settings bound active work and common numerical-library thread pools; they do not impose a hard CPU-percentage, RAM, or GPU-memory cap. Choose the quality tier and concurrency according to available memory.

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
    "queue_timeouts": 0,
    "batches": {
      "limits": {"max_active_jobs": 5, "max_history_jobs": 100},
      "active": 0,
      "total": 0,
      "queued": 0,
      "running": 0,
      "cancelling": 0,
      "completed": 0,
      "completed_with_errors": 0,
      "cancelled": 0,
      "paused": 0,
      "scheduler_running": true,
      "scheduler_error": null
    }
  }
}
```

- `models_ready` — whether the selected backend has its complete required model files and runtime dependencies (`flash` is always ready).
- `resources` — effective limits and conversion counters for this server process; conversion counters reset on restart. `resources.batches` reports retained job counts, active/history limits, scheduler availability, and `scheduler_error` when scheduling fails.

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

Convert a folder synchronously. The request stays open until the selected files finish or resource overload stops the batch. Default behavior remains a flat scan. HTTP 200 can contain per-file failures; inspect `summary` and every `results` entry.

**Request (`application/json`):**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `folder_path` | string | yes | — | Absolute input folder on the server |
| `output_dir` | string | no | `folder_path` | Base output directory |
| `recursive` | bool | no | `false` | Include subdirectories and preserve relative parents in output |
| `extensions` | list of strings | no | `null` | Select supported types, such as `["pdf", ".DOCX"]`; case-insensitive |
| `engine` | string | no | by extension | Engine override |
| `tier` | string | no | `"standard"` | MinerU quality tier |
| `remote` | bool | no | `false` | Use remote MinerU inference |
| `pages` | string | no | `"all"` | Page spec applied to every selected file |

An omitted extension filter selects the union of registered engines' supported types. An empty list, empty type, or unsupported type returns HTTP 400. Use `extensions: ["pdf"]` when selecting PDF pages in a mixed-format folder; non-PDF files and explicit MarkItDown requests require `pages: "all"`.

Input `/docs/team/report.pdf` with folder `/docs`, output `/out`, and recursion enabled writes `/out/team/report.pdf.docs2md/report.md`. Without an explicit output directory, nested results stay next to each input. Two subdirectories containing `report.pdf` retain separate outputs. Always use the returned output paths because very long filenames use a shortened directory prefix and digest.

**Response with a per-file failure:**

```json
{
  "folder": "/path/to/docs",
  "output_dir": "/path/to/output",
  "recursive": false,
  "results": [
    {
      "file": "/path/to/docs/paper.pdf",
      "status": "ok",
      "engine": "mineru",
      "output_path": "/path/to/output/paper.pdf.docs2md/paper.md",
      "images_dir": "/path/to/output/paper.pdf.docs2md/images",
      "fallback": false,
      "fallback_from": null
    },
    {
      "file": "/path/to/docs/locked.docx",
      "status": "error",
      "detail": "Unable to write conversion output: destination is locked",
      "status_code": 409
    }
  ],
  "summary": {"total": 2, "processed": 2, "succeeded": 1, "failed": 1, "pending": 0}
}
```

`summary.total` is the selected file count; `processed` includes success and failure. Resource overload returns HTTP 429 with `Retry-After` and a structured `detail` containing `message`, `folder`, `output_dir`, completed `results`, `summary`, and the remaining `pending_files`, including the file that could not acquire a slot. Preserve completed outputs and resume only pending files instead of blindly resubmitting the whole folder.

### Persistent batch jobs

Use persistent jobs for long or recursive batches. `POST /batches` accepts the same body as `/convert/folder` and returns HTTP 202 with `Location: /batches/<job_id>`. This acknowledges acceptance; poll the location to determine the outcome and inspect each file's engine, fallback, and errors.

```bash
curl -i -X POST http://127.0.0.1:8000/batches \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: research-import-2026-10" \
  -d '{"folder_path":"/path/to/docs","output_dir":"/path/to/output","recursive":true,"extensions":["pdf","docx"]}'

# Use the job_id or Location returned by submission:
curl http://127.0.0.1:8000/batches/JOB_ID
curl 'http://127.0.0.1:8000/batches?limit=20'
```

| Endpoint | Result |
|---|---|
| `POST /batches` | HTTP 202 and the accepted job with a `Location` header |
| `GET /batches?limit=20` | Recent job summaries; `limit` must be 1–50 |
| `GET /batches/{job_id}` | Full progress and per-file items |
| `POST /batches/{job_id}/cancel` | Cooperative cancellation: finish the current file, then stop remaining files |
| `POST /batches/{job_id}/retry` | HTTP 202; retry eligible unfinished items in the same job |

**Example `GET /batches/{job_id}` result:**

```json
{
  "job_id": "00112233445566778899aabbccddeeff",
  "status": "completed_with_errors",
  "folder": "/path/to/docs",
  "output_dir": "/path/to/output",
  "recursive": true,
  "options": {"engine": null, "tier": "standard", "remote": false, "pages": "all"},
  "created_at": "2026-10-11T04:00:00.000+00:00",
  "updated_at": "2026-10-11T04:00:07.000+00:00",
  "cancel_requested": false,
  "total": 2,
  "processed": 2,
  "succeeded": 1,
  "failed": 1,
  "cancelled": 0,
  "pending": 0,
  "running": 0,
  "items": [
    {
      "index": 0,
      "file": "/path/to/docs/team/paper.pdf",
      "relative_path": "team/paper.pdf",
      "output_dir": "/path/to/output/team",
      "status": "succeeded",
      "attempts": 1,
      "status_code": null,
      "detail": null,
      "result": {
        "success": true,
        "engine": "markitdown",
        "output_path": "/path/to/output/team/paper.pdf.docs2md/paper.md",
        "output_dir": "/path/to/output/team",
        "images_dir": null,
        "fallback": true,
        "fallback_from": "mineru",
        "message": "Saved to /path/to/output/team/paper.pdf.docs2md/paper.md"
      }
    },
    {
      "index": 1,
      "file": "/path/to/docs/team/missing.docx",
      "relative_path": "team/missing.docx",
      "output_dir": "/path/to/output/team",
      "status": "failed",
      "attempts": 1,
      "status_code": 404,
      "detail": "File not found: /path/to/docs/team/missing.docx",
      "result": null
    }
  ]
}
```

Progress counts are top-level fields, not a nested `summary`. `failed` includes interrupted files; `processed` includes succeeded, failed/interrupted, and cancelled files. `GET /batches` returns `{"jobs": [...], "limit": 20}` with the same job fields except `items`. Item `attempts` excludes resource-overload admission rejections. An empty selection completes with `total: 0` and `items: []`.

Cancellation preserves completed outputs. A running job enters `cancelling` until its current file finishes; cancellation does not forcibly terminate an active conversion or interrupt final publication. The cancel endpoint accepts no body or `{}` and rejects extra fields. Retrying never reruns `succeeded` files. Retry failed, interrupted, or paused pending items with an empty body or `{}`; include `{"include_cancelled": true}` to resume cancelled items as well. Retry is allowed only for terminal or paused jobs; retrying an active job or one with no eligible items returns HTTP 409. Retry reads the selected source files as they exist at that time.

| Job state | Meaning and next action |
|---|---|
| `queued` | Accepted and waiting for its turn |
| `running` | A file is being processed |
| `cancelling` | Cancellation requested; waiting for the current file to finish |
| `completed` | All selected files succeeded |
| `completed_with_errors` | Processing ended with failed or interrupted items; inspect errors and retry eligible items |
| `cancelled` | Remaining files were cancelled; existing successes remain |
| `paused` | Limited overload retries were exhausted; inspect load and request a retry |

File states are `pending`, `running`, `succeeded`, `failed`, `interrupted`, and `cancelled`. Poll while a job is `queued`, `running`, or `cancelling`; stop and inspect the outcome for the other states. A successful fallback is a `succeeded` item with its actual engine and fallback metadata.

`Idempotency-Key` is optional and accepts 1–128 visible ASCII characters. Choose one key for each intended batch and reuse it only when retrying that submission. Repeating the key with the same normalized request returns the original retained job, including when its input files have changed or disappeared; using that key with different parameters returns HTTP 409. Reuse the key when a submission response is lost. Without a key, every submission creates a new batch and may replace existing outputs for the same inputs. Keys expire when their retained job records are removed.

Batch metadata uses SQLite from Python's standard library in `DOCS2MD_BATCH_STATE_DIR`, defaulting to the ignored project `.docs2md-state/` directory. A submission stores the selected file list; it does not snapshot the files' contents. Service shutdown stops scheduling new files, waits for the current file, and then closes the database. Restart retains successful results and continues pending files; a previously running item becomes `interrupted` and needs an explicit retry. Recovery does not promise exactly-once execution after an abrupt process failure.

If a SQLite scheduling operation fails, the scheduler stops accepting submissions, retries, and cancellations; those operations return HTTP 503. Inspect `/health` → `resources.batches.scheduler_error` and retained job history to diagnose the failure. History remains readable while its database is available. After the underlying storage problem is resolved, restart the service; previously running items become `interrupted` and require an explicit retry.

History holds at most `DOCS2MD_BATCH_HISTORY_LIMIT` job records. Admission removes the oldest eligible terminal metadata when space is needed, preserving converted outputs. Unfinished paused records are retained; if the history limit is full with unfinished work, submission returns HTTP 429. Cancel unneeded paused jobs to make their records eligible for cleanup. Successful items are not automatically revalidated or repaired if their outputs were deleted; submit a new batch to convert those inputs again.

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

**409 — Output or batch conflict:**
```json
{ "detail": "Unable to write conversion output '...': ... Choose a writable output_dir ..." }
```

Batch conflicts include reusing an idempotency key with different parameters, retrying an active job, or having no eligible files to retry.

**413 — Input or batch too large:**
```json
{ "detail": "File exceeds 104857600 bytes: /path/to/document.pdf" }
```

Reduce the document size or batch size, or configure a larger resource limit before starting the service.

**429 — Resource queue busy:**
```json
{ "detail": "Conversion queue is full; retry later" }
```

A full conversion queue or an expired queue wait returns `Retry-After` in seconds. Batch admission also returns 429 when active-job or retained-history limits are full. Retry after that delay; synchronous folder overload includes completed results and pending files in structured `detail`.

**504 — Conversion deadline exceeded:**
```json
{ "detail": "Conversion exceeded 1800 seconds; its worker process tree was stopped" }
```

**503 — Batch manager unavailable:**
```json
{ "detail": "Batch manager is not accepting work" }
```

Preserve the job ID and inspect service availability rather than submitting duplicate jobs.

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
data = resp.json()
if resp.status_code == 429:
    data = data["detail"]  # completed results and pending_files are preserved
else:
    resp.raise_for_status()
print(data["summary"])
for item in data["results"]:
    print(f"{item['file']}: {item['status']} ({item.get('engine', item.get('detail'))})")

# Convert by file upload
with open("/path/to/document.docx", "rb") as f:
    resp = requests.post(
        "http://127.0.0.1:8000/convert/upload",
        files={"file": f},
        data={"engine": "markitdown"},
    )
print(f"Saved to: {resp.json()['output_path']}")
```

For a persistent batch, retain the job ID and poll until the job leaves an active state:

```python
import time
import requests

base_url = "http://127.0.0.1:8000"
submitted = requests.post(
    f"{base_url}/batches",
    json={"folder_path": "/absolute/path/to/docs", "recursive": True},
    headers={"Idempotency-Key": "documents-import-2026-10"},
    timeout=30,
)
submitted.raise_for_status()
job = submitted.json()
print(f"Tracking batch {job['job_id']}")
location = submitted.headers["Location"]
while job["status"] in {"queued", "running", "cancelling"}:
    time.sleep(2)
    polled = requests.get(f"{base_url}{location}", timeout=10)
    polled.raise_for_status()
    job = polled.json()

print(f"{job['status']}: {job['succeeded']} succeeded, {job['failed']} failed")
for item in job["items"]:
    if item["status"] == "succeeded":
        result = item["result"]
        print(item["file"], result["output_path"], result["engine"], result["fallback"])
    else:
        print(item["file"], item["status"], item["status_code"], item["detail"])
```

A `paused` or `cancelled` result may still have pending or cancelled work. Use the explicit retry endpoint when resumption is wanted; polling alone does not resume it.

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

# Recursive PDF-only folder conversion
curl -X POST http://127.0.0.1:8000/convert/folder \
  -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/docs/", "recursive": true, "extensions": ["pdf"]}'

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

The suite covers API errors and fallback, output collisions and rollback, model completeness and backend selection, dependency refresh, isolated startup scripts, admission limits, worker deadlines, process cleanup, upload bounds, batch preflight, recursive planning, and persistent batch lifecycle. Shell tests require Bash (Git Bash works on Windows); only the startup tests mock package managers and systemd commands.

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
│   ├── batch_inputs.py        # Bounded recursive input/output planning
│   ├── batch_jobs.py          # SQLite progress, scheduling, cancel, and retry
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
├── .docs2md-state/        # Local batch metadata (not committed)
├── tests/                 # Unit and real document regressions
├── README.md              # This file
├── .gitignore             # Git ignore rules
├── LICENSE                # MIT License
└── skills/
    └── docs2md/           # Agent skill distributed with the service
        └── SKILL.md
```

## Agent Skill

The service ships an [Agent Skills-compatible skill](https://agentskills.io) at `skills/docs2md/SKILL.md`, outside the ignored `.claude/` and `.mimocode/` directories. Load this directory into your agent to start the service, convert individual documents, or submit and track recursive batch jobs through `http://127.0.0.1:8000`.

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
