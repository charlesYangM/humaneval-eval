# HumanEval Eval

[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/Tests-75%20passed-green.svg)](tests/)
[![Code Style](https://img.shields.io/badge/code%20style-pep8-ff69b4.svg)](https://www.python.org/dev/peps/pep-0008/)

Python-based LLM code generation evaluation framework with concurrent execution, sandboxed code running, structured logging, and SQLite persistence with automatic schema migration.

## Features

- **Sandboxed Execution** — subprocess + resource limits (RLIMIT_CPU/AS/NOFILE/NPROC) + `__builtins__` whitelist
- **Concurrent Evaluation** — ThreadPoolExecutor for parallel problem solving (`--concurrent`)
- **Structured Logging** — 4 levels: quiet / normal / verbose / json (`--log-level`)
- **SQLite Persistence** — `--db` saves results; `--from-db` regenerates reports; auto schema migration
- **Cross-Tabulation Reports** — compact comparison table (cases as rows, models as columns, per-cell `S/T/P/E/Ki/O`)
- **Quality Scoring** — correctness (pass/fail) + code quality (cyclomatic complexity + lines) + serving metrics (TTFT/TPOT/E2E)

## Install

```bash
git clone https://github.com/YOUR_USERNAME/humaneval-eval.git
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

Copy and edit a config file:

```yaml
# config.yaml
model:
  name: "your-model"
  provider: "openai"           # or "custom"
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY" # env var name (NOT the key itself)

# Optional per-model endpoint overrides
model_endpoints:
  deepseek-v4-pro:
    base_url: "https://your-deepseek-endpoint/v1"
    api_key_env: "DEEPSEEK_API_KEY"
```

Set your API key as an environment variable:

```bash
export OPENAI_API_KEY=sk-your-actual-key
# or
export DEEPSEEK_API_KEY=sk-your-deepseek-key
```

The script reads the key from the environment variable named in `api_key_env` — **no hardcoded secrets in config files**.

## Testing

```bash
source .venv/bin/activate
pytest tests/ -v
```

75 tests covering: code extraction, scoring logic, sandbox execution, quality metrics, DB roundtrip, schema migration.

## Database

SQLite DB (`humaneval_eval.db`) is created automatically on first run. Schema auto-migrates on startup — no manual ALTER TABLE needed.

**Do NOT commit the `.db` file.** Add `*.db` to `.gitignore` (already done).

## Examples

This repo includes `solve_deepseek_pro.py` — a reference script demonstrating how to call DeepSeek's API to solve SWE-bench instances programmatically. Use it as a template for integrating the evaluation framework with specific model providers.

```bash
export DEEPSEEK_API_KEY=***
python3 solve_deepseek_pro.py
```

The script can also read the key from `~/.hermes/.env` or `~/.bashrc` automatically.

## Known Limitations

- OS command injection (`os.system`) is NOT fully blocked by the subprocess sandbox (NPROC limit is per-user, not per-process). For full isolation, use Docker (planned).
- When using `openai/oai` provider, the env var is auto-read as `OPENAI_API_KEY`. With `custom` or other providers, `api_key_env` must be set.
- `--from-db` report formatting requires the original schema (not compatible with very old pre-migration DBs).

## License

MIT
