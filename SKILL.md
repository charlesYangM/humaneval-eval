# HumanEval Eval Skill

## What This Is

A Python LLM code generation evaluation framework for HumanEval benchmark.
Concurrent, sandboxed, with SQLite persistence and auto schema migration.

## Trigger Conditions

Load this skill when the user mentions:
- humaneval, evaluation, benchmark, code generation testing
- humaneval_eval.py, evaluation framework
- running evaluation, generating reports from DB

## Quick Reference

### Setup
```bash
cd ~/humaneval-eval  # or wherever cloned
bash install.sh       # one-click: venv + deps + test
# or manually:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run Evaluation
```bash
# Single model
python3 humaneval_eval.py --config config.yaml --models deepseek-v4-pro --num 5 --db

# Multi-model comparison
python3 humaneval_eval.py --config config.yaml --models glm-5.1,deepseek-v4-pro --num 10 --db

# With concurrency
python3 humaneval_eval.py --config config.yaml --models deepseek-v4-pro --num 20 --concurrent 5 --db --log-level verbose

# View past runs
python3 humaneval_eval.py --list

# Report from stored data
python3 humaneval_eval.py --from-db RUN_ID
```

### Config Format
```yaml
model:
  name: "your-model"
  provider: "openai"           # or "custom"
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"  # env var name, NOT the key value

model_endpoints:
  model-name:
    base_url: "https://custom-endpoint/v1"
    api_key_env: "CUSTOM_API_KEY"
```

### Testing
```bash
.venv/bin/pytest tests/ -v --tb=short
```

### Database
- SQLite file: `humaneval_eval.db` (auto-created)
- Schema auto-migrates on startup
- Never commit the .db file

## Project Structure
```
humaneval_eval.py          # main evaluation script (1600+ lines)
config.yaml                # example config (placeholders, see README)
config-maas.yaml           # alternative config example
tests/                     # 75 tests
  ├── conftest.py          # shared fixtures (tmp_db, sample data)
  ├── test_extract_function_code.py
  ├── test_compute_total_score.py
  ├── test_run_test.py     # sandbox execution tests
  ├── test_quality_score.py
  └── test_db_roundtrip.py # includes schema migration tests
README.md                  # project docs
SKILL.md                   # this file — agent usage guide
LICENSE                    # MIT
install.sh                 # one-click installer
requirements.txt           # Python deps
.gitignore                 # excludes db, venv, backup, logs, etc.
backup/                    # .gitignore'd
results/                   # .gitignore'd
```

## Key Design Decisions

1. **Subprocess sandbox (not Docker)** — subprocess + RLIMIT_* is portable; Docker planned for stricter isolation
2. **`api_key_env` not `api_key`** — code reads from env var, never stores keys in config files
3. **Schema migration** — `_SCHEMA_VERSION` constant + `_migrate_db_schema()` function auto-upgrades on startup
4. **Concurrent at problem level** — `evaluate_problem()` is a pure function, safe to parallelize with ThreadPoolExecutor
5. **Clean DB data only** — DB stores raw results, not derived reports; reports are regenerated via `--from-db`

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| `403 Permission denied` | Check `api_key_env` points to correct env var with valid key |
| `Connection refused` | Check `base_url` in config is reachable |
| Tests fail after schema change | Delete old `.db` file (gitignored anyway), rerun `--db` |
| Old DB won't open | Schema auto-migrates; if truly ancient (pre-migration), just delete and re-run |

## Comparison Report Format

Output is a cross-tabulation table where:
- Rows = test cases (problems)
- Columns = models
- Each cell format: `S:score T:TTFTms P:TPOTms/t E:E2Es Ki:input_tokens O:output_tokens`
- Bottom row: p50 / p80 / avg summary
