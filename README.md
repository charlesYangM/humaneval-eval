# HumanEval Eval

[![中文](https://img.shields.io/badge/语言-中文-red.svg)](README.zh.md)

Python-based LLM code generation evaluation framework with concurrent execution, sandboxed code running, structured logging, and SQLite persistence with automatic schema migration.

## Features

- **Sandboxed Execution** — subprocess + resource limits (RLIMIT_CPU/AS/NOFILE/NPROC) + `__builtins__` whitelist
- **Concurrent Evaluation** — ThreadPoolExecutor for parallel problem solving (`--concurrent`)
- **Structured Logging** — 4 levels: quiet / normal / verbose / json (`--log-level`)
- **SQLite Persistence** — `--db` saves results; `--from-db` regenerates reports; auto schema migration
- **Cross-Tabulation Reports** — compact comparison table (cases as rows, models as columns, per-cell `S/T/P/E/K`)
- **Quality Scoring** — correctness (pass/fail) + code quality (cyclomatic complexity + lines) + serving metrics (TTFT/TPOT/E2E)

## Install

```bash
git clone https://github.com/charlesYangM/humaneval-eval.git
cd humaneval-eval

# Option A: one-click install (venv + deps + tests)
bash install.sh

# Option B: manual
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Quick Start

```bash
# Run evaluation (replace 'your-model' with your actual model name)
python3 humaneval_eval.py --config config.yaml --models your-model --num 5 --db

# Run with concurrency
python3 humaneval_eval.py --config config.yaml --models deepseek-v4-pro --num 10 --concurrent 3 --db

# View past runs
python3 humaneval_eval.py --list

# Regenerate report from stored data
python3 humaneval_eval.py --from-db YOUR_RUN_ID
```

## Configuration

Config files reference environment variable names — **never store secrets in config files**.

```yaml
# config.yaml
model:
  name: "your-model"
  provider: "openai"           # or "custom"
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY" # env var name, not the key value

# Optional per-model endpoint overrides
model_endpoints:
  deepseek-v4-pro:
    base_url: "https://your-deepseek-endpoint/v1"
    api_key_env: "DEEPSEEK_API_KEY"
```

Set your API key as an environment variable:

```bash
export OPENAI_API_KEY=*** 
```

## Testing

```bash
source .venv/bin/activate
pytest tests/ -v
```

75 tests covering: code extraction, scoring logic, sandbox execution, quality metrics, DB roundtrip, schema migration.

## Reporting

Output is a cross-tabulation table (cases as rows, models as columns). Each cell:

| Code | Meaning | Description |
|------|---------|-------------|
| **S** | Score (0–10) | 6(pass) + quality(0–4). 0 if failed |
| **T** | TTFT | Time to First Token (lower = faster) |
| **P** | TPOT | Time Per Output Token (lower = faster) |
| **E** | E2E | End-to-end latency |
| **K** | Tokens | Output token count |

Bottom rows: **p50**, **p80**, **avg** summary.

```bash
# Regenerate report from a previous run
python3 humaneval_eval.py --from-db 20260627_175705_deepseek-chat
```

## Database

SQLite DB (`humaneval_eval.db`) is auto-created on first `--db` run. Schema auto-migrates on startup — no manual ALTER TABLE needed.

**Do NOT commit `.db` files.** `*.db` is already in `.gitignore`.

## Examples

`solve_deepseek_pro.py` demonstrates DeepSeek API integration for SWE-bench instances — use it as a template.

```bash
export DEEPSEEK_API_KEY=*** solve_deepseek_pro.py
```

The script can also read the key from `~/.hermes/.env` or `~/.bashrc` automatically.

## Known Limitations

- OS command injection (`os.system()`) is NOT fully blocked by the subprocess sandbox (NPROC is per-user). Full isolation requires Docker (planned).
- When using `openai` provider, the env var is auto-read as `OPENAI_API_KEY`. With `custom` provider, `api_key_env` must be set explicitly.
- `--from-db` requires the current schema. Very old pre-migration DBs may not be compatible.

## License

MIT
