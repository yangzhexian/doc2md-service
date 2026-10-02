# Real document regression corpus

These small files are self-authored synthetic documents, distributed under the
repository's MIT license. They contain no copied documents or user data. The image
is a two-bar chart generated from RGB pixels, and the scanned text uses Pillow's
bundled font. No external documents, network downloads, or operating-system fonts
are used when generating the corpus.

`manifest.json` records every input/resource SHA-256, byte length, engine, page
selection, and expected semantic content. Tests check hashes before conversion,
copy inputs to a temporary directory, and check that every input/resource remains
unchanged afterward. They compare words, page exclusions, table cells, links, and
existing exported image targets, rather than complete Markdown snapshots.

| Document | Coverage |
| --- | --- |
| `two-pages.pdf` | Real native-text PDF with a local chart, distinct markers on each page; whole document, first page, and reverse-index last page |
| `paragraph-and-table.docx` | Heading, paragraph, table cells, UTF-8 Chinese text, and embedded chart |
| `heading-and-image.pptx` | Slide heading/body and embedded chart |
| `inventory.xlsx` | Worksheet cells, numeric values, and UTF-8 Chinese text |
| `inventory.csv` | UTF-8, numeric cells, and a quoted field containing a comma |
| `article-about-concurrency-and-correctness-with-local-resources.html` | Long filename, a relative local image, table, and external destination without fetching it |
| `scanned-text.png` | Actual rasterized text; optional local OCR regression |

The mandatory suite performs 14 actual conversions: 6 with MarkItDown and 8 with
the MinerU CLI at `flash` tier. MinerU native-text PDF/Office/HTML/CSV conversions
require no models. No conversion is mocked and no fallback is accepted. The
MarkItDown HTML test preserves the original relative image reference; MarkItDown
does not export an image directory. Image-file existence checks apply to the assets
exported by MinerU.

Run the corpus with the locked project Python environment:

```sh
python -m unittest discover -s tests -p test_real_documents.py -v
```

Set `DOCS2MD_REAL_REGRESSION_REPORT` to a writable JSON path to retain per-case
engine, dependency versions, elapsed time, assertions, and failure details. Each
case updates this file, including failures. With no override the report lives in
the automatically cleaned test temporary directory.

Tests run in a fresh subprocess with a temporary `MINERU_HOME`, config, cache,
empty model directory, local model source, and offline Hugging Face settings.
Inherited MinerU configuration and remote endpoints are removed. Native CLI parsing
does not start the persistent MinerU server. Tests use only temporary paths and
do not touch the project's runtime configuration or production server.

OCR is intentionally opt-in because it requires heavier packages and weights:

```sh
DOCS2MD_RUN_OCR=1 DOCS2MD_OCR_MODEL_DIR=/absolute/path/to/models \
  python -m unittest discover -s tests -p test_real_documents.py -v
```

For PowerShell, point at an already installed local model directory:

```powershell
$env:DOCS2MD_RUN_OCR = "1"
$env:DOCS2MD_OCR_MODEL_DIR = "D:\path\to\mineru_models"
$env:DOCS2MD_OCR_BACKEND = "torch"  # use onnx for an ONNX model package
venv\Scripts\python.exe -m unittest discover -s tests -p test_real_documents.py -v
Remove-Item Env:DOCS2MD_RUN_OCR, Env:DOCS2MD_OCR_MODEL_DIR, Env:DOCS2MD_OCR_BACKEND
```

The default OCR backend is `onnx`. For an existing Torch installation, also set
`DOCS2MD_OCR_BACKEND=torch`. Missing requested OCR models/dependencies fail the
test; they are never downloaded or silently skipped. Without the opt-in, one OCR
test/report case is explicitly skipped.

Regenerate fixtures deliberately, using the locked environment, and review the
binary/hash changes together:

```sh
python tests/fixtures/real_documents/generate.py
```

The generator fixes document timestamps, ZIP entry timestamps/order, and image
content. Regeneration is byte-deterministic for the same locked library versions.
Generation is separate from ordinary tests so CI always checks the committed
documents, and does not erase an accidental change by regenerating them.
