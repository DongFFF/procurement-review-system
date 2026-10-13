# 中直高校采购需求审查系统

> 知识库驱动的大模型采购需求合规审查与自动修订平台

面向中央部门直属高校（中直高校）的政府采购需求文件审查工具。上传采购需求文件，
系统依据法规知识库逐条审查，输出**可追溯、可执行**的整改意见，用户勾选后**自动修订**文档。

---

## 这个系统解决什么问题

高校采购需求文件在发布前需要经过合规审查，传统做法靠人工逐条比对法规，存在三个痛点：

1. **查不全** —— 政府采购法规体系庞杂（法律、行政法规、部门规章、规范性文件、需求标准），
   人工很难覆盖全部审查点。
2. **说不清** —— 发现问题后，难以准确指出违反了哪一条法规、依据是什么。
3. **改不动** —— 指出问题后，起草人仍需自己琢磨怎么改。

本系统对应给出三个能力：**规则库保证查得全**、**RAG 溯源保证说得清**、**自动修订保证改得动**。

---

## 核心能力

| 能力 | 说明 |
|------|------|
| **法规知识库** | 内置政府采购法规体系，支持手动上传、网址抓取、定时自动更新 |
| **混合检索** | 向量语义检索 + BM25 关键词检索 + 文号/条款号精确加成 |
| **结构化规则库** | 69 条可执行审查规则，覆盖 8 大类别，YAML 维护、热重载 |
| **大模型审查** | 逐规则调用模型判断，强制 JSON 输出，带置信度与人工复核标记 |
| **任意模型接入** | 任何 OpenAI 兼容接口：官方 API、本地 Ollama/vLLM、中转网关、私有化部署 |
| **自动修订** | 勾选意见后回写 Word，保留格式、高亮变更、自动附加修订说明表 |
| **结论清晰** | 总体判定（通过/需整改/不通过）+ 分级问题清单 + 法规依据 + 建议替换文本 |

---

## 快速开始

### 方式一：直接下载 exe（推荐，零门槛）

**[⬇ 下载最新版](https://github.com/DongFFF/procurement-review-system/releases/latest)**

下载 `procurement-review-system-v0.1.0-win64.exe`（约 52 MB），放到任意目录双击运行。
**无需安装 Python、Node.js 或任何环境。**

首次启动约 10-30 秒（会初始化知识库索引），随后浏览器自动打开界面。

然后按 [使用手册](docs/使用手册.md) 配置模型即可开始使用。

### 方式二：源码运行（开发/调试）

```powershell
# 1. 后端
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# 2. 前端
cd frontend
pnpm install
pnpm run build

# 3. 启动（会自动打开浏览器）
cd ..
.\.venv\Scripts\python.exe backend\run_server.py
```

访问 http://127.0.0.1:8756

> Windows 用户也可以直接双击根目录的 **`启动.bat`**，它会自动完成上述全部步骤。

开发态前后端分离（前端热更新）：

```powershell
# 终端 1：后端
.\.venv\Scripts\python.exe backend\run_server.py --reload --no-browser

# 终端 2：前端
cd frontend; pnpm run dev
```

访问 http://localhost:5173

### 方式三：自行打包 exe

```powershell
# PyInstaller（快，无需 Rust）
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm scripts\desktop.spec

# 或 Tauri（安装包更小，需 Rust + MSVC 工具链）
.\scripts\build_desktop.ps1
```

详见 [打包说明](docs/打包说明.md)。

---

## 使用流程

```
① 上传文件          ② 大模型审查            ③ 勾选意见           ④ 自动修订
┌──────────┐      ┌──────────────┐      ┌───────────┐      ┌──────────────┐
│ docx/pdf │  →   │ 逐规则判断    │  →   │ 用户决定   │  →   │ 生成修订版    │
│ 解析分节  │      │ RAG 检索依据  │      │ 采纳哪些   │      │ + 修订说明表  │
└──────────┘      └──────────────┘      └───────────┘      └──────────────┘
```

1. **上传** —— 支持 `.docx` / `.pdf` / `.txt` / `.md`，自动识别「技术参数」「资格条件」
   「评分办法」「商务要求」等章节。
2. **审查** —— 按章节粗筛出适用规则，每条规则独立调用模型，实时显示进度。
3. **查看结论** —— 总体判定 + 按严重程度分级的问题清单，每条含：
   问题位置、原文摘录、法规依据、知识库引用、整改建议、**建议替换文本**、置信度。
4. **勾选** —— 默认勾选「严重」和「高」风险项，可自行增减。
5. **修订** —— 生成修订版 Word（原文件不动）：
   - 黄色高亮 = 已替换内容
   - 绿色高亮 = 新增条款
   - 文末自动附加「修订说明表」，逐条列明改了什么、依据是什么

---

## 项目结构

```
├── backend/                    后端服务（FastAPI）
│   ├── app/
│   │   ├── core/
│   │   │   ├── config.py       配置与模型档案管理
│   │   │   ├── llm.py          大模型统一网关（任意 OpenAI 兼容接口）
│   │   │   ├── knowledge.py    知识库：混合检索（向量 + BM25）
│   │   │   ├── rules.py        规则库加载与粗筛
│   │   │   ├── parser.py       文档解析（docx/pdf/txt）与分节
│   │   │   ├── reviewer.py     审查引擎（核心）
│   │   │   ├── rewriter.py     自动修订与报告导出
│   │   │   ├── fetcher.py      法规抓取与自动更新
│   │   │   └── scheduler.py    定时任务
│   │   ├── api/                REST + SSE 接口
│   │   └── main.py             应用入口
│   ├── requirements.txt
│   └── run_server.py           启动脚本
│
├── frontend/                   前端界面（React + Vite）
│   └── src/
│       ├── App.jsx             应用外壳与导航
│       ├── ReviewPage.jsx      审查工作台（核心流程）
│       ├── KnowledgePage.jsx   知识库管理
│       ├── RulesPage.jsx       审查规则查看
│       ├── HistoryPage.jsx     审查记录
│       ├── SettingsPage.jsx    系统设置
│       ├── api.js              接口客户端
│       └── styles.css          样式
│
├── src-tauri/                  桌面端封装（Tauri 路线，安装包更小）
│
├── knowledge/                  知识库数据（版本化管理）
│   ├── regulations/            法规文件（36 部全文 + 元数据清单）
│   ├── rules/
│   │   └── rules.yaml          69 条结构化审查规则
│   └── samples/                测试样例（含 22 处典型违规点）
│
├── data/                       运行期数据（不入库）
│   ├── config.json             用户配置（含 API Key，仅存本地）
│   ├── uploads/                上传的原始文件
│   ├── exports/                生成的修订版与报告
│   └── index/                  知识库索引
│
├── dist/                       打包产物
│   └── 中直高校采购需求审查系统.exe
│
├── docs/
│   ├── 使用手册.md             面向采购人员的操作说明
│   └── 打包说明.md             桌面端打包指南
│
├── 启动.bat                    绿色版一键启动（自动建环境、装依赖、构建、运行）
│
└── scripts/
    ├── selftest.py             核心模块自检
    ├── test_kb.py              知识库检索与规则粗筛验证
    ├── test_api.py             接口端到端测试
    ├── test_pipeline.py        全流程管道验证（模拟模型，无需 API Key）
    ├── audit_kb.py             知识库质量审计（条款可溯源验证）
    ├── make_sample.py          生成含典型违规点的测试文件
    ├── desktop.spec            PyInstaller 打包配置
    ├── build_desktop.ps1       Tauri 打包脚本
    └── dev.ps1                 开发模式一键启动
```

---

## 知识库设计

### 当前内容

**36 部法规全文**（约 91 万字）+ 38 部法规元数据，索引为 **570 个检索片段**。

核心法规包括：政府采购法及实施条例、政府采购需求管理办法（财库〔2021〕22号）、
87号令、74号令、110号令、中小企业促进办法、进口产品管理办法、
节能环保品目清单、政府采购品目分类目录（3,671 条编码）、
台式机/笔记本/服务器等 5 个需求标准。

**质量保证**：`scripts/audit_kb.py` 会验证规则库引用的每一条法规条款
是否真的能在知识库全文中找到。当前**条款可验证率 100%**（61/61）。

### 为什么用「混合检索」而不是纯向量

政府采购法规有两个检索特点，单一方案都会漏：

| 查询类型 | 例子 | 更擅长的方式 |
|---------|------|------------|
| **语义查询** | "以不合理条件限制供应商" | 向量检索 |
| **精确查询** | "财库〔2021〕22号"、"第二十条" | BM25 关键词 |

系统把两者**加权融合**（默认向量 0.55 / BM25 0.45），并对文号、条款号做额外加成，
兼顾查得全与查得准。实测首位命中正确法规 90%、前 5 位 100%。

### 嵌入向量策略

- 优先使用配置的模型 API 的 `/embeddings` 接口；
- 不可用时自动降级为**本地字符 n-gram 哈希向量**（离线、零依赖、速度快）。

这保证了**没有外网、没有 Embedding 模型时系统依然可用**。

### 法规自动更新

- 内置 9 个权威来源（gov.cn / ccgp.gov.cn 等），可在「知识库」页查看。
- 策略：抓取正文 → 比对内容哈希 → **有变化才写入**，不会重复入库。
- 后台定时检查（默认 24 小时），更新后自动重建索引。
- 所有更新留痕，可在「更新日志」追溯。

> ⚠️ 政府网站结构各异且可能改版，自动抓取的正文**建议人工核验**后再用于正式审查。

---

## 审查规则库

规则以 YAML 存放于 `knowledge/rules/`，可直接编辑，改完在界面点「重载规则库」即生效。

### 规则结构

```yaml
- code: LEGAL_QUALIFICATION_REGION        # 唯一代码
  name: 特定区域业绩限制                    # 规则名
  category: legality                      # 类别
  severity: blocker                       # blocker/high/medium/low
  applies_to: [资格条件, 评分办法]          # 适用文档部位
  legal_basis: 《政府采购法实施条例》第二十条第四项   # 法定依据
  check_prompt: |                         # 给大模型的检查指令
    检查资格条件和评分办法中是否出现以特定行政区域...
  violation_example: 投标人须提供近三年在北京市高校的同类项目业绩不少于3份...
  suggestion_template: 删除地域或行业限定表述，改为...
```

### 规则分布（69 条）

| 类别 | 条数 | 类别 | 条数 |
|------|-----|------|-----|
| 合法合规性 | 9 | 政策功能 | 8 |
| 需求完整性 | 11 | 预算与资金 | 5 |
| 技术参数倾向性 | 7 | 程序与时限 | 11 |
| 商务条款合理性 | 9 | 评分办法 | 9 |

严重级别：`blocker` 22 条、`high` 27 条、`medium` 19 条、`low` 1 条。

### 扩展规则

在 `knowledge/rules/` 下新建 `.yaml` 文件（会自动合并），或在现有文件中追加条目：

```yaml
- code: MY_CUSTOM_RULE
  name: 自定义审查点
  category: completeness
  severity: medium
  applies_to: [商务要求]
  legal_basis: 《XXX办法》第X条
  check_prompt: 检查……
  violation_example: ……
  suggestion_template: ……
```

> `code` 必须全局唯一，重复会被跳过并在界面提示。

---

## 模型接入

支持**任何 OpenAI 兼容接口**。已预置 DeepSeek、OpenAI、通义千问、智谱、Kimi、
硅基流动、Ollama 等，也可自定义。

| 场景 | base_url 示例 |
|------|--------------|
| DeepSeek | `https://api.deepseek.com/v1` |
| OpenAI | `https://api.openai.com/v1` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 本地 Ollama | `http://localhost:11434/v1` |
| 中转网关 | `https://your-gateway.com/v1` |

**建议**：审查任务对稳定性要求高，推荐使用推理能力较强的模型
（如 DeepSeek-V3、GPT-4o、Qwen-Max），温度设为 `0～0.2`。

系统具备以下容错能力：
- 自动重试（指数退避），网络抖动不影响整体审查
- 模型不支持 `response_format` 时自动降级为提示词约束
- JSON 输出鲁棒解析（兼容 markdown 围栏、前后废话、尾随逗号）
- 单条规则失败不拖垮整体，会在报告中标注

---

## 打包桌面端

两条路线，共用同一套后端与前端代码。

### 路线 A：PyInstaller（推荐，无需 Rust）

```powershell
cd frontend; pnpm run build; cd ..
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm scripts\desktop.spec
```

产物：`dist/中直高校采购需求审查系统.exe`（约 52 MB，单文件）

### 路线 B：Tauri（安装包更小，约 10-15 MB）

需要 Rust 工具链 + MSVC C++ 生成工具：

```powershell
winget install Rustlang.Rustup
winget install Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"

cd frontend; pnpm run build; cd ..
.\scripts\build_desktop.ps1
```

产物位于 `src-tauri/target/release/bundle/nsis/`。

详见 [docs/打包说明.md](docs/打包说明.md)。

---

## 测试

```powershell
$env:PYTHONIOENCODING="utf-8"   # Windows 控制台中文输出

.\.venv\Scripts\python.exe scripts\selftest.py      # 核心模块自检（8 项）
.\.venv\Scripts\python.exe scripts\test_kb.py       # 知识库检索与规则粗筛
.\.venv\Scripts\python.exe scripts\test_api.py      # 接口端到端（8 项）
.\.venv\Scripts\python.exe scripts\test_pipeline.py # 全流程管道（36 项，无需 API Key）
.\.venv\Scripts\python.exe scripts\audit_kb.py      # 知识库质量审计
```

### 当前状态

| 检查项 | 结果 |
|-------|------|
| 核心模块自检 | 通过 |
| 接口端到端测试 | 8/8 通过 |
| 全流程管道测试 | 36/36 通过 |
| 知识库条款可验证率 | **100%**（61/61） |
| 检索首位命中率 | 90% |
| 检索前 5 位命中率 | 100% |
| 打包 exe 实机验证 | 通过 |

`test_pipeline.py` 用模拟模型跑通全链路，并刻意注入真实模型常见的脏输出
（markdown 围栏、缺字段、尾随逗号、低置信度、原文摘录被改写），
验证系统的容错能力——**不需要 API Key 即可验证整条管线**。

---

## 数据与隐私

- **所有数据都在本机**：法规库、上传文件、审查结果、生成的文档均存于本地目录。
- **API Key 仅存本地** `data/config.json`，已加入 `.gitignore`，不会提交到仓库。
- 调用模型时，只有**待审查文档内容**和**法规依据**会发送给你配置的模型服务商。
  如对数据出境敏感，建议使用本地部署模型（Ollama / vLLM）。

---

## 已知限制

1. **扫描件 PDF 无法解析** —— 图片型 PDF 提取不到文本，会给出明确提示，建议先 OCR 或改用 Word。
2. **自动修订仅支持 .docx** —— PDF 无法回写，系统会提示另存为 docx。
3. **模型判断有不确定性** —— 系统对低置信度结论标记「建议人工复核」，
   **审查结果应作为辅助参考，最终结论需人工确认**。
4. **自动抓取的法规需核验** —— 见上文知识库章节。
5. **审查结果存于内存** —— 重启程序后「审查记录」会清空，但已导出的文件保留在 `data/exports/`。

---

## 免责声明

本系统为**辅助工具**，基于公开法规和通用规则提供审查建议，不构成法律意见。
采购需求文件的最终合规性由采购人及其主管部门负责。使用前请结合本单位适用的
内部管理制度和上级规定，并对审查结论进行人工确认。

---

## 许可

本项目采用 [MIT License](LICENSE) 开源。

你可以自由使用、修改、分发，包括商业用途，只需保留版权声明。
欢迎其他高校和采购单位直接采用或二次开发。

### 关于法规文本

`knowledge/regulations/` 下的法规文件均来自政府官方网站（gov.cn / ccgp.gov.cn /
mof.gov.cn 等）。根据《中华人民共和国著作权法》第五条，法律、法规，国家机关的
决议、决定、命令和其他具有立法、行政、司法性质的文件不适用著作权法，
因此这些文本可自由使用。

### 贡献

欢迎提交 Issue 和 Pull Request，特别是：

- **补充法规全文** —— 目前 36 部法规有全文，另有 5 部仅有元数据
  （见 `scripts/audit_kb.py` 的输出）
- **新增审查规则** —— 在 `knowledge/rules/` 下添加 YAML 即可
- **适配本单位制度** —— 各高校内部采购管理规定差异较大，欢迎贡献规则包

提交前请运行测试：

```powershell
.\.venv\Scripts\python.exe scripts\test_pipeline.py   # 无需 API Key
.\.venv\Scripts\python.exe scripts\audit_kb.py
```

