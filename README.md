# HumanEval Eval · 大模型代码生成评估框架

[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/Tests-75%20passed-green.svg)](tests/)
[![Code Style](https://img.shields.io/badge/code%20style-pep8-ff69b4.svg)](https://www.python.org/dev/peps/pep-0008/)

**English | [中文](#features)**

Python-based LLM code generation evaluation framework with concurrent execution, sandboxed code running, structured logging, and SQLite persistence with automatic schema migration.

基于 Python 的 LLM 代码生成评估框架，支持并发执行、沙箱安全运行、结构化日志、SQLite 持久化存储及自动 schema 迁移。

---

## Features · 功能特性

- **Sandboxed Execution 沙箱执行** — subprocess + resource limits (RLIMIT_CPU/AS/NOFILE/NPROC) + `__builtins__` whitelist
- **Concurrent Evaluation 并发评估** — ThreadPoolExecutor 多线程并行解题（`--concurrent`）
- **Structured Logging 结构化日志** — 4 级输出：quiet / normal / verbose / json（`--log-level`）
- **SQLite Persistence 持久化存储** — `--db` 保存结果；`--from-db` 从数据库重新生成报告；自动 schema 迁移
- **Cross-Tabulation Reports 交叉对比报告** — 表格格式（用例为行、模型为列，每格 `S/T/P/E/K`）
- **Quality Scoring 多层次评分** — 功能正确性(6分) + 代码质量(圈复杂度+行数，0-4分) = 总分 0-10

## Install · 安装

```bash
git clone https://github.com/charlesYangM/humaneval-eval.git
cd humaneval-eval

# Option A: one-click install（一键安装）
bash install.sh

# Option B: manual（手动安装）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Quick Start · 快速开始

```bash
# Run evaluation（运行评估，替换 your-model 为实际模型名）
python3 humaneval_eval.py --config config.yaml --models your-model --num 5 --db

# With concurrency（并发执行）
python3 humaneval_eval.py --config config.yaml --models deepseek-v4-pro --num 10 --concurrent 3 --db

# View past runs（查看历史运行记录）
python3 humaneval_eval.py --list

# Regenerate report（从存储数据重新生成报告）
python3 humaneval_eval.py --from-db YOUR_RUN_ID
```

## Configuration · 配置说明

配置文件中**不存储密钥**，密钥通过环境变量传入。`api_key_env` 指定环境变量名，`api_key_env` 后面的值不是密钥本身。

```yaml
# config.yaml
model:
  name: "your-model"
  provider: "openai"           # or "custom"（自定义 endpoint）
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY" # 环境变量名（不是密钥本身）

# 可选：为不同模型指定不同 endpoint
model_endpoints:
  deepseek-v4-pro:
    base_url: "https://your-deepseek-endpoint/v1"
    api_key_env: "DEEPSEEK_API_KEY"
```

配置好 API key：

```bash
export OPENAI_API_KEY=sk-you...-key
# or
export DEEPSEEK_API_KEY=sk-you...-key
```

## Testing · 测试

```bash
source .venv/bin/activate
pytest tests/ -v
```

75 tests covering: code extraction, scoring logic, sandbox execution, quality metrics, DB roundtrip, schema migration.
75 个测试覆盖：代码提取、评分逻辑、沙箱执行、质量指标、DB 读写回环、schema 迁移。

## Reporting · 报告格式

报告是交叉对比表，用例为行、模型为列。每个指标含义：

| Code 代码 | Stands for 含义 | Description 说明 |
|-----------|-----------------|------------------|
| **S** | Score 总分 (0–10) | 6(功能通过) + 质量(0-4). 不通过则 0 分 |
| **T** | TTFT 首包延迟 | Time to First Token，越低响应越快 |
| **P** | TPOT 生成速率 | Time Per Output Token，越低推理越快 |
| **E** | E2E 端到端延迟 | 从请求到完整回复的总耗时 |
| **K** | Tokens 输出 token 数 | 模型生成的总 token 量 |

底部汇总行：**p50**（中位数）、**p80**（80分位）、**avg**（平均值）

示例报告：

```
| 用例 | 能力 | deepseek-chat |
|------|------|---------------|
| HumanEval/0 | 条件判断 | 8.0 T:626ms P:10.5ms/t E:2.4s K:333 |
| **p50** | | 8.0 T:626ms P:10.5ms/t E:2.2s K:287 |
| **p80** | | 8.0 T:929ms P:13.1ms/t E:2.4s K:333 |
| **avg** | | 8.0 T:726ms P:11.2ms/t E:2.1s K:278 |
```

```bash
# 从数据库重新生成报告
python3 humaneval_eval.py --from-db 20260627_175705_deepseek-chat
```

## Database · 数据库

SQLite DB (`humaneval_eval.db`) is auto-created on first run. Schema auto-migrates — no manual ALTER TABLE needed.
SQLite 数据库在首次运行 `--db` 时自动创建。Schema 在启动时自动迁移，无需手动执行 ALTER TABLE。

**不要将 `.db` 文件提交到 Git。** `.gitignore` 已包含 `*.db`。

## Examples · 示例脚本

`solve_deepseek_pro.py` is a reference script demonstrating DeepSeek API integration for SWE-bench instances — use it as a template.
`solve_deepseek_pro.py` 是一个参考示例，演示如何调用 DeepSeek API 解决 SWE-bench 实例，可作为自定义集成模板。

```bash
export DEEPSEEK_API_KEY=*** solve_deepseek_pro.py
```

The script can also read the key from `~/.hermes/.env` or `~/.bashrc` automatically.
脚本也会自动从 `~/.hermes/.env` 或 `~/.bashrc` 读取密钥。

## Known Limitations · 已知限制

- **OS command injection**: `os.system()` is NOT fully blocked by subprocess sandbox (NPROC is per-user). Full isolation requires Docker (planned).
  `os.system()` 在 subprocess 沙箱中不能完全被阻止（NPROC 是用户级限制），完全隔离需要 Docker（计划中）。
- **Provider auto-detection**: `openai` provider auto-reads `OPENAI_API_KEY`. For `custom` provider, `api_key_env` must be explicitly set.
  `openai` provider 会自动读取 `OPENAI_API_KEY` 环境变量。`custom` provider 必须显式设置 `api_key_env`。
- **Schema compatibility**: `--from-db` requires the current schema. Very old pre-migration DBs may not be compatible.
  `--from-db` 报告需要当前 schema，极早期的旧 DB 可能不兼容。

## License · 开源协议

MIT
