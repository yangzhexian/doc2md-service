---
description: Convert documents (PDF, DOCX, PPTX, XLSX, HTML, CSV, images) to Markdown via a local MinerU API service. Use when the user asks to convert a document, paper, or file to markdown, or wants to extract text from a PDF with formulas and tables preserved.
---

# Document to Markdown Converter

Convert documents to Markdown using the local `docs2md` FastAPI service.
PDFs are processed with MinerU (GPU-accelerated OCR, formula & table
recognition); all other formats use MarkItDown.

## Locating the Project

This skill can be installed globally. To find the project directory, check in
order:

1. The `DOCS2MD_HOME` environment variable (set it to the project root).
2. The directory where this skill file lives, adjusted to the project root
   (the skill is at `<project>/.claude/skills/doc2md/SKILL.md`).
3. Common clone paths: `~/doc2md-service`, `~/docs2md`, `~/projects/doc2md-service`.

If you can't locate the project, ask the user where it is.

## Prerequisites

The service must be running at `http://127.0.0.1:8000`. If it is not already
running, start it:

```bash
# From the project root (one-click):
./start.sh          # Linux / macOS
start.bat           # Windows

# Or manually:
uvicorn converter_service:app --host 127.0.0.1 --port 8000
```

Verify the service is up:

```bash
curl http://127.0.0.1:8000/health
```

## Usage

### Convert a single file

```bash
curl -X POST http://127.0.0.1:8000/convert/path \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/absolute/path/to/document.pdf"}'
```

**Response:**
```json
{
  "status": "success",
  "markdown": "# Title\n\nContent...",
  "engine": "mineru",
  "output_path": "/path/to/document/docs2md/document.md"
}
```

The converted `.md` file is saved to `<source_parent>/docs2md/<stem>.md`
by default. Override with `"output_dir": "/custom/output/dir"`.

### Batch-convert a folder

```bash
curl -X POST http://127.0.0.1:8000/convert/folder \
  -H "Content-Type: application/json" \
  -d '{"folder_path": "/absolute/path/to/folder", "recursive": true}'
```

**Response:**
```json
{
  "status": "success",
  "total": 5,
  "succeeded": 5,
  "failed": 0,
  "results": [
    {"file_path": "...", "stem": "paper", "engine": "mineru", "output_path": "...", "status": "success"}
  ]
}
```

Output files go to `<folder>/docs2md/<relative_path>/<stem>.md` by default.

### Convert an uploaded file

```bash
curl -X POST http://127.0.0.1:8000/convert/upload \
  -F "file=@/path/to/document.pdf" \
  -F "use_mineru_for_pdf=true"
```

### Python usage

```python
import requests

# Single file
r = requests.post("http://127.0.0.1:8000/convert/path", json={
    "file_path": "/absolute/path/to/paper.pdf",
    "output_dir": "/output/dir",       # optional
})
print(r.json()["markdown"])

# Batch folder
r = requests.post("http://127.0.0.1:8000/convert/folder", json={
    "folder_path": "/absolute/path/to/docs/",
    "recursive": True,
})
for item in r.json()["results"]:
    print(f"{item['stem']}: {item['status']} ({item['engine']})")
```

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` / `folder_path` | string | required | Absolute path to file or folder |
| `output_dir` | string | `<source>/docs2md/` | Custom output directory |
| `use_mineru_for_pdf` | bool | `true` | Use MinerU for PDF (falls back to MarkItDown) |
| `mineru_method` | string | `"ocr"` | `ocr`, `txt`, or `auto` |
| `mineru_lang` | string | `"en"` | OCR language: `en`, `ch`, etc. |
| `recursive` | bool | `true` | (Folder only) Recurse into subdirectories |

## Instructions

When asked to convert a document:

1. **Locate the project** — use `DOCS2MD_HOME`, the skill file path, or common
   clone paths. If you can't find it, ask the user.
2. Check that the service is running (`GET /health`). If it is not,
   start it from the project root with `./start.sh` (Linux/macOS) or
   `start.bat` (Windows).
3. Determine the absolute file path.
4. Call the appropriate endpoint (`/convert/path` for a single file,
   `/convert/folder` for a batch).  Use `requests` or `curl`.
5. Report the result: engine used, output path, and a brief preview
   of the converted content. If the conversion fails, show the error
   and suggest fallback options (e.g., disabling MinerU).
6. If the caller wants custom output location, pass `output_dir` in
   the request body.
