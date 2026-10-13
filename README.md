# Procurement Requirement Review System

> A knowledge-base-driven platform for LLM-powered compliance review and automatic revision of government procurement requirement documents.

**English** | [简体中文](docs/README.zh-CN.md)

[![CI](https://github.com/DongFFF/procurement-review-system/actions/workflows/ci.yml/badge.svg)](https://github.com/DongFFF/procurement-review-system/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/DongFFF/procurement-review-system)](https://github.com/DongFFF/procurement-review-system/releases/latest)

---

## ⚠️ Please read first

This system is built for the **Chinese public procurement regulatory framework**
(《中华人民共和国政府采购法》 and its subordinate rules). Two consequences:

- **The knowledge base is Chinese law.** All 36 regulation texts, the 69 review
  rules, and every legal citation are in Simplified Chinese. There is no English
  legal content, and the review conclusions are only meaningful against Chinese law.
- **The user interface is in Simplified Chinese.** It targets Chinese university
  procurement officers and department reviewers.

The **codebase, architecture, and API are language-agnostic** and fully documented
below. If you want to adapt this pattern to another jurisdiction's procurement
rules, the rule engine and RAG pipeline are reusable as-is — you would replace
`knowledge/` with your own regulations and rule pack. Contributions in that
direction are welcome.

---

## What this solves

In Chinese universities, a procurement requirement document must pass a compliance
review before it can be published. The traditional process is manual, clause-by-clause
comparison against regulations, and it has three failure modes:

1. **Incomplete coverage** — the regulatory stack is layered (national law,
   administrative regulations, ministry rules, normative documents, procurement
   standards). Manual review cannot cover every checkpoint.
2. **Unverifiable findings** — when a reviewer flags an issue, it is hard to state
   precisely which clause was violated.
3. **No path to a fix** — after a problem is identified, the drafter still has to
   figure out how to rewrite it.

This system addresses each: **a structured rule library for coverage**, **RAG
citation tracing for verifiability**, and **automatic document revision for fixability**.

---

## Core capabilities

| Capability | Description |
|-----------|-------------|
| **Regulation knowledge base** | 36 full regulation texts (~910k characters), plus upload, URL ingestion, and scheduled auto-update |
| **Hybrid retrieval** | Vector semantic search + BM25 keyword search + exact-match boost for document numbers and article numbers |
| **Structured rule library** | 69 executable review rules across 8 categories; YAML-maintained with hot reload |
| **LLM review engine** | Per-rule model evaluation, forced JSON output, confidence scores, human-review flags |
| **Any model provider** | Any OpenAI-compatible endpoint: official APIs, local Ollama/vLLM, gateway proxies, on-premise deployments |
| **Automatic revision** | Writes selected findings back into Word, preserves formatting, highlights changes, appends a change log |
| **Clear conclusions** | Overall verdict (fail / needs revision / pass) + severity-ranked issue list + legal basis + ready-to-apply replacement text |

---

## Quick start

### Option 1: Download the executable (zero setup)

**[⬇ Download latest release](https://github.com/DongFFF/procurement-review-system/releases/latest)**

Download `procurement-review-system-v0.1.0-win64.exe` (~52 MB) and double-click it.
**No Python, Node.js, or any runtime required.**

First launch takes 10–30 seconds (it builds the knowledge base index), then the
browser opens automatically. Then configure a model — see the
[Chinese user manual](docs/使用手册.md) (the UI is in Chinese).

### Option 2: Run from source

```powershell
# 1. Backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# 2. Frontend
cd frontend
pnpm install
pnpm run build

# 3. Launch (opens browser automatically)
cd ..
.\.venv\Scripts\python.exe backend\run_server.py
```

Open http://127.0.0.1:8756

> On Windows you can also just double-click **`启动.bat`** in the repository root;
> it performs all of the above automatically.

For frontend hot-reload during development:

```powershell
# Terminal 1 — backend
.\.venv\Scripts\python.exe backend\run_server.py --reload --no-browser

# Terminal 2 — frontend
cd frontend; pnpm run dev
```

Open http://localhost:5173

### Option 3: Build the executable yourself

```powershell
# PyInstaller (fast, no Rust required)
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm scripts\desktop.spec

# Or Tauri (smaller installer, requires Rust + MSVC toolchain)
.\scripts\build_desktop.ps1
```

See [packaging guide](docs/打包说明.md) (Chinese).

---

## Review workflow

```
① Upload            ② LLM review          ③ Select findings     ④ Auto-revise
┌──────────┐      ┌──────────────┐      ┌───────────┐      ┌──────────────┐
│ docx/pdf │  →   │ per-rule     │  →   │ user picks │  →   │ revised doc  │
│ parse &  │      │ RAG-grounded │      │ which to   │      │ + change log │
│ section  │      │ judgement    │      │ accept     │      │ appended     │
└──────────┘      └──────────────┘      └───────────┘      └──────────────┘
```

1. **Upload** — `.docx` / `.pdf` / `.txt` / `.md`. The parser auto-detects sections
   such as technical specifications, qualification requirements, scoring method,
   and commercial terms.
2. **Review** — applicable rules are pre-filtered by section, then each rule is
   evaluated independently with live progress reporting.
3. **Read conclusions** — overall verdict plus a severity-ranked issue list. Each
   finding includes: location, verbatim source excerpt, legal basis, retrieved
   regulation excerpts, remediation advice, **ready-to-apply replacement text**,
   and a confidence score.
4. **Select** — blocker and high-severity findings are pre-selected; adjust freely.
5. **Revise** — generates a revised Word file (the original is never modified):
   - yellow highlight = replaced content
   - green highlight = newly inserted clauses
   - a change log table is appended, listing what changed and on what legal basis

---

## Project structure

```
├── backend/                    Backend service (FastAPI)
│   ├── app/
│   │   ├── core/
│   │   │   ├── config.py       Configuration & model profiles
│   │   │   ├── llm.py          Unified LLM gateway (any OpenAI-compatible API)
│   │   │   ├── knowledge.py    Knowledge base: hybrid retrieval (vector + BM25)
│   │   │   ├── rules.py        Rule library loading & pre-filtering
│   │   │   ├── parser.py       Document parsing (docx/pdf/txt) & section detection
│   │   │   ├── reviewer.py     Review engine (core)
│   │   │   ├── rewriter.py     Automatic revision & report export
│   │   │   ├── fetcher.py      Regulation fetching & auto-update
│   │   │   └── scheduler.py    Scheduled jobs
│   │   ├── api/                REST + SSE endpoints
│   │   └── main.py             Application entry point
│   ├── requirements.txt
│   └── run_server.py           Launcher
│
├── frontend/                   Web UI (React + Vite)
│   └── src/
│       ├── App.jsx             Shell & navigation
│       ├── ReviewPage.jsx      Review workbench (core flow)
│       ├── KnowledgePage.jsx   Knowledge base management
│       ├── RulesPage.jsx       Rule browser
│       ├── HistoryPage.jsx     Review history
│       ├── SettingsPage.jsx    Settings
│       ├── api.js              API client
│       └── styles.css
│
├── src-tauri/                  Desktop shell (Tauri path, smaller installer)
│
├── knowledge/                  Knowledge base data (version controlled)
│   ├── regulations/            36 full regulation texts + metadata manifest
│   ├── rules/
│   │   └── rules.yaml          69 structured review rules
│   └── samples/                Test sample (22 seeded violations)
│
├── data/                       Runtime data (not tracked)
│   ├── config.json             User config (contains API key, local only)
│   ├── uploads/                Uploaded source files
│   ├── exports/                Generated revisions & reports
│   └── index/                  Knowledge base index
│
├── dist/                       Build output
│   └── 中直高校采购需求审查系统.exe
│
├── docs/
│   ├── README.zh-CN.md         简体中文 README
│   ├── 使用手册.md              User manual
│   └── 打包说明.md              Packaging guide
│
├── 启动.bat                    One-click launcher (portable build)
│
└── scripts/
    ├── selftest.py             Core module self-test
    ├── test_kb.py              Retrieval & rule pre-filter verification
    ├── test_api.py             End-to-end API tests
    ├── test_pipeline.py        Full pipeline test (mock model, no API key needed)
    ├── audit_kb.py             Knowledge base quality audit (citation traceability)
    ├── make_sample.py          Generates a test document with seeded violations
    ├── desktop.spec            PyInstaller spec
    ├── build_desktop.ps1       Tauri build script
    └── dev.ps1                 Dev-mode launcher
```

---

## Knowledge base design

### Current contents

**36 full regulation texts** (~910k characters) plus metadata for 38 regulations,
indexed into **570 retrieval chunks**.

Core regulations include: the Government Procurement Law and its implementing
regulations, the Procurement Requirement Management Measures (财库〔2021〕22号),
Ministry of Finance Orders No. 87 / 74 / 110, the SME promotion measures, the
imported-product management measures, energy-saving and eco-label product lists,
the procurement item classification catalogue (3,671 codes), and five procurement
requirement standards (desktop PCs, laptops, servers, operating systems, databases).

**Quality assurance**: `scripts/audit_kb.py` verifies that every legal citation
referenced by the rule library can actually be located in the knowledge base text.
Current **citation traceability: 100%** (61/61).

### Why hybrid retrieval instead of pure vector search

Chinese procurement regulations have two distinct retrieval patterns, and a single
approach misses one of them:

| Query type | Example | Better suited method |
|-----------|---------|---------------------|
| **Semantic** | "restricting suppliers with unreasonable conditions" | Vector search |
| **Exact** | "财库〔2021〕22号", "第二十条" | BM25 keyword search |

The system **weighted-fuses** both (default: vector 0.55 / BM25 0.45) and applies an
additional boost for document numbers and article numbers. Measured: 90% top-1
accuracy, 100% top-5 accuracy for the correct regulation.

### Embedding strategy

- Prefers the `/embeddings` endpoint of the configured model API.
- Falls back automatically to a **local character n-gram hash vectorizer**
  (offline, zero dependencies, fast).

This guarantees the system **remains usable without internet access or an
embedding model**.

### Regulation auto-update

- 9 authoritative sources built in (gov.cn, ccgp.gov.cn, etc.).
- Strategy: fetch text → compare content hash → **write only when changed**.
- Background checks (default every 24 hours); index rebuilds automatically.
- Every update is logged and traceable in the UI.

> ⚠️ Government sites vary in structure and may change layout. Fetched text
> **should be human-verified** before relying on it for formal review.

---

## Review rule library

Rules live in `knowledge/rules/` as YAML. Edit them directly, then click
"reload rules" in the UI — no restart needed.

### Rule structure

```yaml
- code: LEGAL_QUALIFICATION_REGION        # unique code
  name: 特定区域业绩限制                    # rule name
  category: legality                      # category
  severity: blocker                       # blocker/high/medium/low
  applies_to: [资格条件, 评分办法]          # applicable document sections
  legal_basis: 《政府采购法实施条例》第二十条第四项   # legal basis
  check_prompt: |                         # instruction sent to the LLM
    检查资格条件和评分办法中是否出现以特定行政区域...
  violation_example: 投标人须提供近三年在北京市高校的同类项目业绩不少于3份...
  suggestion_template: 删除地域或行业限定表述，改为...
```

### Distribution (69 rules)

| Category | Count | Category | Count |
|----------|-------|----------|-------|
| Legality | 9 | Policy functions | 8 |
| Completeness | 11 | Budget & funding | 5 |
| Technical bias | 7 | Procedure & timing | 11 |
| Commercial terms | 9 | Scoring method | 9 |

By severity: `blocker` 22, `high` 27, `medium` 19, `low` 1.

### Adding your own rules

Create a new `.yaml` file under `knowledge/rules/` (files are merged
automatically), or append to an existing one:

```yaml
- code: MY_CUSTOM_RULE
  name: Custom checkpoint
  category: completeness
  severity: medium
  applies_to: [商务要求]
  legal_basis: 《XXX办法》第X条
  check_prompt: Check whether ...
  violation_example: ...
  suggestion_template: ...
```

> `code` must be globally unique; duplicates are skipped and reported in the UI.

---

## Model integration

Supports **any OpenAI-compatible endpoint**. Presets are included for DeepSeek,
OpenAI, Qwen (DashScope), Zhipu GLM, Moonshot Kimi, SiliconFlow, and Ollama;
custom endpoints are fully supported.

| Provider | base_url example |
|----------|-----------------|
| DeepSeek | `https://api.deepseek.com/v1` |
| OpenAI | `https://api.openai.com/v1` |
| Qwen (DashScope) | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Local Ollama | `http://localhost:11434/v1` |
| Gateway proxy | `https://your-gateway.com/v1` |

**Recommendation**: review tasks benefit from stronger reasoning models
(e.g. DeepSeek-V3, GPT-4o, Qwen-Max) with temperature `0–0.2`.

Robustness features:

- Automatic retry with exponential backoff — transient network issues do not break a review
- Graceful degradation when the model does not support `response_format`
- Robust JSON parsing (handles markdown fences, surrounding prose, trailing commas)
- A single failing rule does not abort the whole review; it is flagged in the report
- Local endpoints automatically bypass the system proxy

---

## Building the desktop app

Two paths sharing the same backend and frontend code.

### Path A: PyInstaller (recommended, no Rust needed)

```powershell
cd frontend; pnpm run build; cd ..
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm scripts\desktop.spec
```

Output: `dist/中直高校采购需求审查系统.exe` (~52 MB, single file)

### Path B: Tauri (smaller installer, ~10–15 MB)

Requires the Rust toolchain and MSVC C++ build tools:

```powershell
winget install Rustlang.Rustup
winget install Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"

cd frontend; pnpm run build; cd ..
.\scripts\build_desktop.ps1
```

Output: `src-tauri/target/release/bundle/nsis/`

See the [packaging guide](docs/打包说明.md) (Chinese).

---

## Testing

```powershell
$env:PYTHONIOENCODING="utf-8"   # Chinese console output on Windows

.\.venv\Scripts\python.exe scripts\selftest.py      # core modules (8 checks)
.\.venv\Scripts\python.exe scripts\test_kb.py       # retrieval & rule pre-filter
.\.venv\Scripts\python.exe scripts\test_api.py      # end-to-end API (8 checks)
.\.venv\Scripts\python.exe scripts\test_pipeline.py # full pipeline (36 checks, no API key)
.\.venv\Scripts\python.exe scripts\audit_kb.py      # knowledge base quality audit
```

### Current status

| Check | Result |
|-------|--------|
| Core module self-test | pass |
| End-to-end API tests | 8/8 pass |
| Full pipeline tests | 36/36 pass |
| Citation traceability | **100%** (61/61) |
| Retrieval top-1 accuracy | 90% |
| Retrieval top-5 accuracy | 100% |
| Packaged executable, on real hardware | pass |

`test_pipeline.py` runs the entire pipeline against a mock model and deliberately
injects the kinds of malformed output real models produce (markdown fences, missing
fields, trailing commas, low confidence, paraphrased source excerpts) to verify
fault tolerance — **no API key required to validate the whole pipeline**.

CI runs on GitHub Actions across ubuntu/windows × Python 3.11/3.12, plus a frontend
build job and a rule-library YAML validation job.

---

## Data & privacy

- **All data stays local**: regulation library, uploaded files, review results, and
  generated documents are stored in local directories.
- **API keys are stored locally only**, in `data/config.json`, which is gitignored.
- When calling a model, only the **document under review** and the **retrieved
  regulation excerpts** are sent to the provider you configure. If data residency
  is a concern, use a locally deployed model (Ollama / vLLM).

---

## Known limitations

1. **Scanned PDFs cannot be parsed** — image-only PDFs yield no text. The system
   says so explicitly; run OCR or supply a Word file instead.
2. **Automatic revision supports `.docx` only** — PDF cannot be written back to.
3. **Model judgement is probabilistic** — low-confidence findings are flagged for
   human review. **Review output is advisory; final conclusions require human
   confirmation.**
4. **Auto-fetched regulations need verification** — see the knowledge base section.
5. **Review results are held in memory** — the history list clears on restart, but
   exported files persist in `data/exports/`.

---

## Disclaimer

This system is an **assistive tool**. It provides review suggestions based on public
regulations and generic rules, and **does not constitute legal advice**. Ultimate
compliance responsibility for a procurement requirement document rests with the
procuring entity and its supervising authority. Always confirm review conclusions
against the internal rules and superior regulations applicable to your institution.

---

## License

Released under the [MIT License](LICENSE).

You are free to use, modify, and distribute this software, including commercially,
provided the copyright notice is retained.

### On the regulation texts

Files under `knowledge/regulations/` are sourced from official government websites
(gov.cn, ccgp.gov.cn, mof.gov.cn, etc.). Under Article 5 of the Copyright Law of the
People's Republic of China, laws, regulations, and documents of a legislative,
administrative, or judicial nature are not subject to copyright, so these texts may
be freely redistributed.

### Contributing

Issues and pull requests are welcome. Especially useful:

- **Completing regulation texts** — 36 regulations have full text; 5 currently have
  metadata only (see `scripts/audit_kb.py` output)
- **Adding review rules** — drop a YAML file into `knowledge/rules/`
- **Adapting to other jurisdictions** — the rule engine and RAG pipeline are
  jurisdiction-agnostic; a rule pack for another country's procurement framework
  would be a valuable contribution
- **Translating the UI** — the frontend is not yet internationalized

Please run the tests before submitting:

```powershell
.\.venv\Scripts\python.exe scripts\test_pipeline.py   # no API key needed
.\.venv\Scripts\python.exe scripts\audit_kb.py
```
