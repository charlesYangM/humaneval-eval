# HumanEval Eval · 大模型代码生成评估框架

[![English](https://img.shields.io/badge/lang-English-blue.svg)](README.md)

基于 Python 的 LLM 代码生成评估框架，支持并发执行、沙箱安全运行、结构化日志、SQLite 持久化存储及自动 schema 迁移。

---

## 功能特性

- **沙箱执行** — subprocess 子进程 + 资源限制 (RLIMIT_CPU/AS/NOFILE/NPROC) + `__builtins__` 白名单
- **并发评估** — ThreadPoolExecutor 多线程并行解题（`--concurrent`）
- **结构化日志** — 4 级输出：quiet / normal / verbose / json（`--log-level`）
- **持久化存储** — `--db` 保存结果；`--from-db` 从数据库重新生成报告；自动 schema 迁移
- **交叉对比报告** — 表格格式（用例为行、模型为列，每格 `S/T/P/E/K`）
- **多层次评分** — 功能正确性(6分) + 代码质量(圈复杂度+行数，0-4分) = 总分 0-10

## 安装

```bash
git clone https://github.com/charlesYangM/humaneval-eval.git
cd humaneval-eval

# 方式一：一键安装（自动建 venv + 装依赖 + 跑测试）
bash install.sh

# 方式二：手动安装
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 快速开始

```bash
# 运行评估（替换 your-model 为实际模型名）
python3 humaneval_eval.py --config config.yaml --models your-model --num 5 --db

# 并发执行
python3 humaneval_eval.py --config config.yaml --models deepseek-v4-pro --num 10 --concurrent 3 --db

# 查看历史运行记录
python3 humaneval_eval.py --list

# 从存储数据重新生成报告
python3 humaneval_eval.py --from-db YOUR_RUN_ID
```

## 配置说明

配置文件中**不存储密钥**，密钥通过环境变量传入。`api_key_env` 指定环境变量名，`api_key_env` 后面的值不是密钥本身。

```yaml
# config.yaml
model:
  name: "your-model"
  provider: "openai"           # 或 "custom"（自定义 endpoint）
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY" # 环境变量名（不是密钥本身）

# 可选：为不同模型指定不同 endpoint
model_endpoints:
  deepseek-v4-pro:
    base_url: "https://your-deepseek-endpoint/v1"
    api_key_env: "DEEPSEEK_API_KEY"
```

设置 API key：

```bash
export OPENAI_API_KEY=*** 
```

## 测试

```bash
source .venv/bin/activate
pytest tests/ -v
```

75 个测试覆盖：代码提取、评分逻辑、沙箱执行、质量指标、DB 读写回环、schema 迁移。

## 报告格式

报告是交叉对比表（用例为行、模型为列）。每个指标含义：

| 代码 | 含义 | 说明 |
|------|------|------|
| **S** | 总分 (0–10) | 6(功能通过) + 质量(0-4). 不通过则 0 分 |
| **T** | TTFT 首包延迟 | Time to First Token，越低响应越快 |
| **P** | TPOT 生成速率 | Time Per Output Token，越低推理越快 |
| **E** | E2E 端到端延迟 | 从请求到完整回复的总耗时 |
| **K** | Tokens 输出量 | 模型生成的 token 总数 |

底部汇总行：**p50**（中位数）、**p80**（80分位）、**avg**（平均值）

示例：

```
| 用例 | 能力 | deepseek-chat |
|------|------|---------------|
| HumanEval/0 | 条件判断 | 8.0 T:626ms P:10.5ms/t E:2.4s K:333 |
| **p50** | | 8.0 T:626ms P:10.5ms/t E:2.2s K:287 |
```

```bash
# 从数据库重新生成报告
python3 humaneval_eval.py --from-db 20260627_175705_deepseek-chat
```

## 数据库

SQLite 数据库 (`humaneval_eval.db`) 在首次执行 `--db` 时自动创建。Schema 在启动时自动迁移，无需手动执行 ALTER TABLE。

**不要将 `.db` 文件提交到 Git。** `.gitignore` 已包含 `*.db`。

## 示例脚本

`solve_deepseek_pro.py` 演示如何通过 DeepSeek API 解决 SWE-bench 实例，可作为自定义集成的模板。

```bash
export DEEPSEEK_API_KEY=*** solve_deepseek_pro.py
```

脚本也会自动从 `~/.hermes/.env` 或 `~/.bashrc` 读取密钥。

## 已知限制

- `os.system()` 在 subprocess 沙箱中不能完全被阻止（NPROC 是用户级限制），完全隔离需要 Docker（计划中）
- `openai` provider 会自动读取 `OPENAI_API_KEY`。`custom` provider 必须显式设置 `api_key_env`
- `--from-db` 报告需要当前 schema，极早期的旧 DB 可能不兼容

## 开源协议

MIT
