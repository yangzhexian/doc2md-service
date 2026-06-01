# Document to Markdown Converter Service

基于 [MinerU](https://github.com/opendatalab/MinerU) 和 [MarkItDown](https://github.com/microsoft/markitdown) 构建的本地文档转 Markdown HTTP 微服务，支持 PDF、DOCX、PPTX、XLSX、HTML、CSV、图片等多种格式。

## 特性

- **双引擎 PDF 转换**：复杂排版的 PDF（含表格、公式）优先使用 MinerU（`magic-pdf`）处理；若 MinerU 不可用或转换失败，自动降级为 MarkItDown。
- **多格式支持**：DOCX、PPTX、XLSX、HTML、CSV、JSON、XML、图片、音频等格式统一由 MarkItDown 转换。
- **两种调用方式**：支持本地文件路径直接转换（`POST /convert/path`）和文件上传转换（`POST /convert/upload`）。
- **Swagger UI**：内置 `/docs` 交互式 API 文档，方便人工调试。
- **健康检查**：`GET /health` 返回服务状态及 MinerU 可用性。

## 环境要求

- Python 3.12+
- 操作系统：Linux / macOS / Windows（WSL 推荐）
- MinerU 可选（仅 PDF 复杂排版需要）

## 依赖安装

### 1. 基础依赖

```bash
pip install -r requirements.txt
```

### 2. MinerU（可选，用于 PDF 高精度转换）

MinerU 是一个专门处理复杂 PDF 文档（含表格、公式、多栏布局）的开源工具。安装步骤请参考官方文档：

- [MinerU 官方安装指南](https://github.com/opendatalab/MinerU)

简要步骤（Linux / WSL）：

```bash
# 安装 MinerU
pip install magic-pdf

# 下载模型权重（首次运行时会自动下载，也可以手动预先下载）
# 详见官方文档
```

如果不需要 PDF 高精度转换，或 MinerU 安装失败，服务会自动降级为 MarkItDown 处理所有 PDF，不影响其他格式的转换。

> **Windows 用户注意**：MinerU 在原生 Windows 上兼容性有限，建议使用 WSL2 运行本服务。

## 快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
uvicorn converter_service:app --host 127.0.0.1 --port 8000
```

启动后访问：
- Swagger UI：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health

## API 文档

### GET /health

健康检查，返回服务状态和 MinerU 是否可用。

**响应示例**：
```json
{
  "status": "ok",
  "mineru_available": true
}
```

---

### POST /convert/path

通过本地文件绝对路径转换文档。

**请求体**（JSON）：
```json
{
  "file_path": "/home/user/documents/paper.pdf",
  "use_mineru_for_pdf": true
}
```

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | 本地文件的绝对路径 |
| `use_mineru_for_pdf` | bool | 否 | PDF 文件是否优先使用 MinerU（默认 true） |

**响应示例**：
```json
{
  "status": "success",
  "markdown": "# Paper Title\n\n## Abstract\n...",
  "engine": "mineru",
  "detail": null
}
```

---

### POST /convert/upload

上传文件进行转换。

**请求**（multipart/form-data）：
| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `file` | file | 是 | 要上传的文件 |
| `use_mineru_for_pdf` | bool | 否 | PDF 文件是否优先使用 MinerU（默认 true） |

**响应示例**：
```json
{
  "status": "success",
  "markdown": "# Document Content\n...",
  "engine": "markitdown",
  "detail": null
}
```

**错误响应**（HTTP 400/404）：
```json
{
  "detail": "File not found: /path/to/nonexistent.pdf"
}
```

## 调用示例

### Python

```python
import requests

# 方式一：本地路径
resp = requests.post(
    "http://127.0.0.1:8000/convert/path",
    json={"file_path": "/absolute/path/to/document.pdf", "use_mineru_for_pdf": True},
)
print(resp.json()["markdown"])

# 方式二：上传文件
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
# 本地路径方式
curl -X POST http://127.0.0.1:8000/convert/path \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/path/to/document.pdf"}'

# 上传方式
curl -X POST http://127.0.0.1:8000/convert/upload \
  -F "file=@/path/to/document.docx"
```

## 支持的文件格式

| 类别 | 扩展名 | 处理引擎 |
|------|--------|----------|
| PDF | `.pdf` | MinerU → MarkItDown（自动降级） |
| Word | `.docx` | MarkItDown |
| PowerPoint | `.pptx` | MarkItDown |
| Excel | `.xlsx` | MarkItDown |
| 网页 | `.html`, `.htm` | MarkItDown |
| 数据 | `.csv`, `.json`, `.xml` | MarkItDown |
| 图片 | `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.webp` | MarkItDown |
| 音频 | `.mp3`, `.wav`, `.ogg`, `.wma`, `.m4a`, `.flac` | MarkItDown |
| 压缩包 | `.zip` | MarkItDown |

## 项目结构

```
doc2md-service/
├── converter_service.py   # 主服务代码（FastAPI 应用）
├── requirements.txt       # Python 依赖
├── README.md              # 本文件
├── .gitignore             # Git 忽略规则
└── LICENSE                # MIT 许可证
```

## 注意事项

1. **MinerU 资源要求**：MinerU 对内存和 CPU 有一定要求，大型 PDF 转换可能耗时较长（默认超时 600 秒）。如果服务器资源有限，可设置 `use_mineru_for_pdf=false` 直接使用 MarkItDown。
2. **Windows 兼容性**：MinerU 在原生 Windows 上可能存在兼容性问题，建议使用 WSL2 或 Docker。
3. **文件路径**：`POST /convert/path` 要求传入文件的**绝对路径**，服务进程需要有读取该文件的权限。
4. **临时文件清理**：`POST /convert/upload` 上传的文件和 MinerU 生成的中间文件会在处理完成后自动清理。
5. **安全建议**：此服务设计为本地使用，请勿直接暴露在公网上。如需远程访问，请添加认证和鉴权机制。
6. **音频转换**：音频文件的 Markdown 转录功能需要安装额外的语音识别依赖，请参考 [MarkItDown 文档](https://github.com/microsoft/markitdown)。

## 许可证

MIT License — 详见 [LICENSE](LICENSE) 文件。
