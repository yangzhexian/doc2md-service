---
name: docs2md
description: Convert documents (PDF, DOCX, PPTX, XLSX, HTML, CSV, images) to Markdown using the local docs2md service at http://127.0.0.1:8000. Use when the user asks to convert a file, paper, or folder to markdown, or extract text from PDFs with formulas/tables preserved.
---

# docs2md

Local document-to-Markdown service. PDFs use MinerU by default; other formats
use MarkItDown. The engine can be overridden per request.

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
  `<parent_of_file_path>/<stem>/<stem>.md`.
- Path separators: use `/` or escaped `\\` on Windows.

### Upload file

`POST /convert/upload` accepts `multipart/form-data`.

```bash
curl -X POST http://127.0.0.1:8000/convert/upload \
  -F "file=@/absolute/path/to/file.pdf" \
  -F "output_dir=/absolute/path/to/output"
```

- `output_dir` is optional. If omitted, results are saved under the service's
  configured upload output directory (default: `<project_root>/output/<stem>/`).
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
| `engine` | `mineru` | `mineru`, `markitdown`, or `auto` |
| `method` | `auto` | MinerU parse method: `auto`, `ocr`, `txt` |
| `lang` | `""` | MinerU language hint: `ch`, `en`, etc. |
| `formula_enable` | `true` | Enable MinerU formula recognition |
| `table_enable` | `true` | Enable MinerU table recognition |

## Response

### Single-file response

```json
{
  "success": true,
  "engine": "mineru",
  "output_path": "</absolute/path/to/output/stem/stem.md>",
  "output_dir": "</absolute/path/to/output>",
  "images_dir": "</absolute/path/to/output/stem/images>",
  "fallback": false,
  "message": "Saved to </absolute/path/to/output/stem/stem.md>"
}
```

`engine` is `mineru` or `markitdown` (or `markitdown (fallback from mineru)`).
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
      "output_path": "</absolute/path/to/output/paper/paper.md>",
      "images_dir": "</absolute/path/to/output/paper/images>"
    }
  ]
}
```

## Output layout

For every converted file, the service creates a folder named after the input
file stem and stores everything inside it:

- Markdown: `<output_dir>/<stem>/<stem>.md`
- Images (MinerU only): `<output_dir>/<stem>/images/`
- Image references in the Markdown are relative to the Markdown file
  (`![](images/...)`), so the folder can be moved or opened as a unit.

Override the base directory with `output_dir`.
