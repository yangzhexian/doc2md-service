---
name: docs2md
description: Convert documents (PDF, DOCX, PPTX, XLSX, HTML, CSV, images) to Markdown using the local docs2md service at http://127.0.0.1:8000. Use when the user asks to convert a file, paper, or folder to markdown, or extract text from PDFs with formulas/tables preserved.
---

# docs2md

Local document-to-Markdown service. PDFs and Office/native formats use MinerU
4.x by default; other formats use MarkItDown. The engine can be overridden per
request.

The HTTP API **does not return the converted Markdown content**; it only returns
conversion status, the engine used, and the path where the `.md` file was saved.
Read the Markdown from that path afterwards.

## Check / start service

```bash
curl http://127.0.0.1:8000/health
```

If not running, start it:

| Context | Windows | macOS / Linux |
|---|---|---|
| Inside project dir | `start.bat` or `start.vbs` | `./start.sh` |
| With `DOCS2MD_HOME` | `%DOCS2MD_HOME%\start.bat` | `$DOCS2MD_HOME/start.sh` |

Wait 5s, recheck `/health`. If still down, stop.

## Convert

### Single file by path

`POST /convert/path` accepts a JSON body.

```bash
curl -X POST http://127.0.0.1:8000/convert/path \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "</absolute/path/to/file>",
    "output_dir": "</absolute/path/to/output>"
  }'
```

- `file_path` is required.
- `output_dir` is optional. If omitted, results are saved under
  `<parent_of_file_path>/<filename>.docs2md/<stem>.md`.
- Path separators: use `/` or escaped `\\` on Windows.

### Upload file

`POST /convert/upload` accepts `multipart/form-data`.

```bash
curl -X POST http://127.0.0.1:8000/convert/upload \
  -F "file=@/absolute/path/to/file.pdf" \
  -F "output_dir=/absolute/path/to/output"
```

- `output_dir` is optional. If omitted, results are saved under the service's
  configured upload output directory (default: `<project_root>/output/<filename>.docs2md/`).
  Set `DOCS2MD_UPLOAD_OUTPUT_DIR` to override the default.

### Batch folder

`POST /convert/folder` accepts a JSON body.

```bash
curl -X POST http://127.0.0.1:8000/convert/folder \
  -H "Content-Type: application/json" \
  -d '{
    "folder_path": "</absolute/path/to/folder>",
    "output_dir": "</absolute/path/to/output>"
  }'
```

- `output_dir` is optional. If omitted, results are saved next to each input
  file inside `folder_path`.

## Common options

| Field | Default | Description |
|---|---|---|
| `output_dir` | see endpoint notes | Base directory for output |
| `engine` | by extension | `mineru` or `markitdown` (omit to auto-route) |
| `tier` | `standard` | MinerU quality tier: `flash` (fast / native docs), `basic` (OCR/formula/table), `standard` (complex layout, best default), `advanced` (hard docs, higher cost) |
| `remote` | `false` | Use remote MinerU inference (`--remote`) |
| `pages` | `all` | Page spec, 1-based: `all`, `1-5`, `1-5,8`, `r3-r1` (rN = Nth from end) |

PDF and images support every tier. Office / HTML / CSV / EPUB / RTF /
OpenDocument / OFD always parse as whole-document `flash`.

For academic papers with formulas and dense tables, keep the default
`tier=standard` (or use `tier=advanced` when `standard` is not enough).
Check `/health` → `models_ready` before requesting a tier that needs models.

Page subsets require PDF inputs and MinerU. If MinerU fails on a subset,
the API returns an error instead of falling back to a whole-document parse.
Explicit MarkItDown requests and non-PDF inputs require `pages: "all"`.

## Response

### Single-file response

```json
{
  "success": true,
  "engine": "mineru",
  "output_path": "</absolute/path/to/output/document.pdf.docs2md/document.md>",
  "output_dir": "</absolute/path/to/output>",
  "images_dir": "</absolute/path/to/output/document.pdf.docs2md/images>",
  "fallback": false,
  "fallback_from": null,
  "message": "Saved to </absolute/path/to/output/document.pdf.docs2md/document.md>"
}
```

`engine` is `mineru` or `markitdown`. On MinerU failure the service falls back
automatically: `fallback: true`, `fallback_from: "mineru"`.
Report the engine and output path. On error: `{"detail": "..."}`.

Do **not** expect a `markdown` field in the response. Read the saved file from
`output_path` when you need the content.

### Folder response

```json
{
  "folder": "</absolute/path/to/folder>",
  "output_dir": "</absolute/path/to/output>",
  "results": [
    {
      "file": "</absolute/path/to/folder/paper.pdf>",
      "status": "ok",
      "engine": "mineru",
      "output_path": "</absolute/path/to/output/paper.pdf.docs2md/paper.md>",
      "images_dir": "</absolute/path/to/output/paper.pdf.docs2md/images>",
      "fallback": false,
      "fallback_from": null
    }
  ]
}
```

## Output layout

For every converted file, the service creates a folder named
`<filename>.docs2md` using the complete input filename, including its
extension, and stores everything inside it:

- Markdown: `<output_dir>/<filename>.docs2md/<stem>.md`
- Images (MinerU only): `<output_dir>/<filename>.docs2md/images/`
- Image references in the Markdown are relative to the Markdown file
  (`![](images/...)`), so the folder can be moved or opened as a unit.

Override the base directory with `output_dir`.

Very long output directory names use a shortened prefix plus digest. Always
use the returned `output_path` rather than constructing it from the filename.
