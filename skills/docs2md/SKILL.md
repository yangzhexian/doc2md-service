---
name: docs2md
description: Convert documents or folders to Markdown with the local docs2md service, and track, cancel, or retry its persistent batch conversions. Use for PDF text extraction with formulas/tables preserved and PDF, Office, HTML, CSV, or image conversion.
---

# docs2md

Convert local documents through `http://127.0.0.1:8000`. PDFs and Office/native formats use MinerU 4.x by default; other supported formats use MarkItDown. The API returns saved paths and conversion metadata. Read Markdown from `output_path` when content is needed; there is no `markdown` response field.

## Locate and start the service

Load this tracked `skills/docs2md/` directory directly. Locate the server using `DOCS2MD_HOME`, the current project, or the repository containing this skill.

```bash
curl http://127.0.0.1:8000/health
```

| Context | Windows | macOS / Linux |
|---|---|---|
| Inside project directory | `start.bat` or `start.vbs` | `./start.sh` |
| With `DOCS2MD_HOME` | `%DOCS2MD_HOME%\start.bat` | `$DOCS2MD_HOME/start.sh` |

First startup installs locked dependencies and can take several minutes. Poll `/health` while setup progresses. If startup fails, inspect `launcher.log` / `uvicorn.log` on Windows or launcher output on Unix. `/health` reports per-tier model readiness and current resource limits; check it before selecting a model-dependent tier.

## Choose the conversion mode

| Task | Endpoint | Completion behavior |
|---|---|---|
| One file already on the server | `POST /convert/path` | Wait for the saved result |
| One uploaded file | `POST /convert/upload` | Wait for the saved result |
| A small folder in one request | `POST /convert/folder` | Inspect summary and each result, including partial progress on overload |
| A long or recursive folder batch | `POST /batches` | Acceptance returns HTTP 202; poll the returned job until its outcome is known |

Use paths on the server, with `/` or escaped `\\` separators on Windows. An HTTP 200 folder response can contain failed files; HTTP 202 means a batch was accepted and does not mean its files converted successfully.

### Single file or upload

```bash
curl -X POST http://127.0.0.1:8000/convert/path \
  -H "Content-Type: application/json" \
  -d '{"file_path":"/absolute/path/to/paper.pdf","output_dir":"/absolute/path/to/output"}'

curl -X POST http://127.0.0.1:8000/convert/upload \
  -F "file=@/absolute/path/to/paper.pdf" \
  -F "output_dir=/absolute/path/to/output"
```

Single-file requests default to the input file's parent. Uploads default to the service's persistent upload output directory, normally `<project>/output/`, configured through `DOCS2MD_UPLOAD_OUTPUT_DIR`.

### Folder selection and output layout

Both folder endpoints accept `folder_path`, optional `output_dir`, `recursive` (default `false`), and `extensions` (default all supported types). Filters accept values such as `"pdf"` or `".PDF"`, ignoring case and optional leading dots. Empty or unsupported extension filters return HTTP 400. Select `extensions: ["pdf"]` when applying a page subset to a mixed-format folder.

```bash
curl -X POST http://127.0.0.1:8000/convert/folder \
  -H "Content-Type: application/json" \
  -d '{"folder_path":"/absolute/path/to/docs","output_dir":"/absolute/path/to/output","recursive":true,"extensions":["pdf","docx"]}'
```

Results use `<output_dir>/<relative_parent>/<filename>.docs2md/<stem>.md`; extracted MinerU images live beside that Markdown in `images/`. Without `output_dir`, each result stays beside its input. Recursive scans preserve nested parent paths so identical filenames in separate subdirectories do not overwrite each other. The scanner skips generated `.docs2md` directories, a separate output subtree inside the input folder, directory symlinks, and Windows junctions. File symlinks retain their alias filenames. Long directory names use a shortened prefix and digest; use returned paths rather than constructing them.

Synchronous folder responses contain `results` with `status: "ok"` or `"error"`, and `summary` with `total`, `processed`, `succeeded`, `failed`, and `pending`. On HTTP 429, `detail` preserves completed `results`, `summary`, and `pending_files`; honor `Retry-After` and resume pending files. Avoid blindly resubmitting already successful files.

### Persistent batches

Choose one `Idempotency-Key` per intended batch and reuse it when retrying that submission after a lost response. The key must contain 1–128 visible ASCII characters. Reuse the same key and normalized request to retrieve the original retained job; a different request with the same key returns HTTP 409. Without a key, another submission creates a new batch and may overwrite outputs for the same source files.

```bash
curl -i -X POST http://127.0.0.1:8000/batches \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: documents-import-2026-10" \
  -d '{"folder_path":"/absolute/path/to/docs","output_dir":"/absolute/path/to/output","recursive":true}'

# Use the Location or job_id returned by submission:
curl http://127.0.0.1:8000/batches/JOB_ID
curl 'http://127.0.0.1:8000/batches?limit=20'
```

The response uses `status` for the job state and top-level `total`, `processed`, `succeeded`, `failed`, `cancelled`, `pending`, and `running` counts. Each `items` entry has `file`, `relative_path`, `status`, `attempts`, `status_code`, `detail`, and `result`. Read `result.output_path`, `result.engine`, and fallback metadata for succeeded items; failed items have no result. `failed` includes interrupted files, and `processed` also includes cancellations.

If the user requested completion, retain the job ID and poll `Location`, then inspect per-file outputs, actual engine, fallback, and errors. If the user requested only submission, report the accepted job and its location. The list endpoint returns recent summaries with a limit of 1–50; fetch the individual job for its complete items.

| Job state | Handling |
|---|---|
| `queued` / `running` / `cancelling` | Continue polling when completion is requested |
| `completed` | Report successful outputs and any fallback usage |
| `completed_with_errors` | Inspect failed or interrupted items; preserve successes |
| `cancelled` | Report preserved successes and cancelled remainder |
| `paused` | Overload retries were exhausted; inspect load before an explicit retry |

File states are `pending`, `running`, `succeeded`, `failed`, `interrupted`, and `cancelled`. A successful fallback is still `succeeded`; report the actual engine and `fallback_from`, without presenting primary-engine failure as primary-engine success.

```bash
# Cancel future files; the currently scheduled file finishes safely:
curl -X POST http://127.0.0.1:8000/batches/JOB_ID/cancel

# Retry failed/interrupted items and pending items of a paused job:
curl -X POST http://127.0.0.1:8000/batches/JOB_ID/retry

# Also resume cancelled items when that is wanted:
curl -X POST http://127.0.0.1:8000/batches/JOB_ID/retry \
  -H "Content-Type: application/json" \
  -d '{"include_cancelled":true}'
```

Retry operates on a terminal or paused job and preserves `succeeded` items; HTTP 409 means the job is still active or there are no eligible items. Source contents are read when a file runs or retries. If a successful output was deleted or its input changed, submit a new batch to reconvert it. Cancellation is cooperative and stops future files after the current file completes; it does not interrupt final publication. The cancel endpoint accepts no body or `{}` and rejects extra fields. Cancel when the user requests stopping the batch.

After one item receives three resource-overload responses, the server sets its job to `paused`; delays between attempts honor `Retry-After` and are capped at 60 seconds. Keep any further retries bounded; if a job repeatedly pauses or the underlying file error persists, report completed and pending work instead of retrying forever.

Batch metadata defaults to the ignored project `.docs2md-state/` directory. Restart preserves successful results and continues pending items; an item that was running at interruption becomes `interrupted` and needs retry. History retention can remove old terminal metadata and its idempotency key, while converted files remain. Recovery does not guarantee exactly-once conversion after abrupt failure.

If SQLite scheduling fails, submit, retry, and cancel return HTTP 503. Check `resources.batches.scheduler_error` in `/health` and read retained history while the database remains available. Preserve the job ID and state directory for diagnosis; after the storage problem is resolved, restart the service and explicitly retry interrupted items.

## Conversion options

| Field | Default | Description |
|---|---|---|
| `engine` | by extension | `mineru` or `markitdown`; omit to auto-route |
| `tier` | `standard` | MinerU `flash`, `basic`, `standard`, or `advanced` |
| `remote` | `false` | Use remote MinerU inference |
| `pages` | `all` | PDF page spec: `all`, `1-5`, `1-5,8`, or `r3-r1` |

PDF and images support every tier. Office / HTML / CSV / EPUB / RTF / OpenDocument / OFD parse as whole-document `flash`. Academic papers generally use `standard`; use `advanced` for difficult layouts. PDF page subsets require MinerU. Non-PDF files and explicit MarkItDown requests require `pages: "all"`. A failed subset request does not fall back to a whole-document parse. Missing model/configuration and output-write errors also do not trigger fallback.

Single-file success returns `success`, `engine`, `output_path`, `output_dir`, `images_dir`, `fallback`, `fallback_from`, and `message`. Folder successes include the corresponding output and fallback metadata per file. Return saved paths and identify any failures; read the Markdown only when the user's task needs its content.

## Resource limits and errors

All modes share the server's conversion resource budget. Default limits allow one active conversion, four pending conversions, a 30-second queue wait, a 1,800-second worker deadline, 100 MiB per file, 100 selected files, and 10,000 examined entries per folder scan. Batch jobs default to five active jobs and 100 retained records. Consult `/health` for the actual configured limits; `resources.batches` reports job/history limits, retained status counts, and scheduler availability. JSON conversion/batch bodies are capped at 64 KiB; multipart bodies allow 1 MiB overhead above the file limit.

| Status | Handling |
|---|---|
| `400` / `422` | Correct paths, filters, or conversion options; missing models may require service setup |
| `404` | Verify the input path or retained job ID |
| `409` | Inspect the conflict: output permissions, idempotency mismatch, or nothing eligible to retry |
| `413` | Reduce file size or narrow/split the folder selection |
| `429` | Honor `Retry-After`; preserve partial results, inspect job/history limits, and avoid unbounded retries |
| `504` | The worker tree was stopped; use a smaller PDF page range, cheaper tier, or suitable server deadline |
| `503` | Preserve the job ID and inspect service availability; avoid submitting duplicate jobs |

Avoid sending one parallel request per file to bypass resource controls. Do not remove outputs or batch state merely to clear a reported error. If history is full with unfinished paused jobs, cancel unneeded jobs when requested so their metadata can become eligible for retention cleanup.
