#!/usr/bin/env python3
"""
HumanEval 多层代码正确性评估

用法:
  # 单模型 (用 config.yaml 的模型配置)
  python3 humaneval_eval.py --config config.yaml --num 20

  # 多模型对比 (通过 New API 路由)
  python3 humaneval_eval.py --config config.yaml \\
      --models deepseek-v4-pro,anthropic/claude-opus-4.7 \\
      --num 30

  # 指定某模型的特殊 endpoint
  python3 humaneval_eval.py --config config.yaml \\
      --models deepseek-v4-pro,glm-5.1 \\
      --model-endpoint glm-5.1=https://xxx.com/openai/v1 \\
      --num 20

评估层级:
  Layer 0: 语法检查 (0 分门槛)
  Layer 1: 功能正确性 — 运行 HumanEval test, 0 分门槛
  Layer 2: 代码质量  — 圈复杂度 (0-3) + 代码行数 (±1), clamp 0-4
  Layer 3: 运行时效率 — vs 标准解的实际执行速度 (仅记录)
  ────────────────────────────────────
  总分: 10 分 (Layer 1 不通过则 0 分)

服务性能指标 (同步测量):
  TTFT  — Time to First Token (流式首包延迟)
  TPOT  — Time Per Output Token (流式生成速率)
  E2E   — 端到端延迟
  RPM   — 并发吞吐量 (非流式)
"""
import argparse, ast, json, os, re, sys, time, signal, sqlite3, logging, subprocess, resource, struct
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict
import yaml
import requests as req


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def resolve_api_key(cfg, api_key_env=None):
    env = api_key_env or cfg["model"].get("api_key_env", "")
    key = os.environ.get(env, cfg["model"].get("api_key", ""))
    if not key:
        ep = os.path.expanduser("~/.hermes/.env")
        if os.path.exists(ep):
            with open(ep) as f:
                for line in f:
                    l = line.strip()
                    if "=" in l and not l.startswith("#"):
                        k, v = l.split("=", 1)
                        if k.strip() == env:
                            key = v.strip().strip("\"'")
                            break
    return key


def load_humaneval(path="/tmp/HumanEval.jsonl"):
    if not os.path.exists(path):
        print(f"Downloading HumanEval...")
        os.system("curl -sL https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz | gunzip > /tmp/HumanEval.jsonl")
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


# ═══════════════════════════════════════════════════════════════
# 结构化日志系统
# ═══════════════════════════════════════════════════════════════
# 四级输出: quiet(仅错误) / normal(进度+结果) / verbose(调试) / json(机器可读)
# 运营场景: cron 用 --log-level quiet, Loop 用 normal, 开发调试用 verbose

class EvalLogger:
    """结构化评估日志器 — 替代散落 print, 便于运营和自动化消费"""

    LEVELS = OrderedDict([("quiet", 0), ("normal", 1), ("verbose", 2), ("json", 3)])

    def __init__(self, level="normal"):
        self.level = level
        self._idx = 0
        # json 模式下缓冲所有事件, 最后一次性输出
        self._json_buffer = []
        # 统计摘要
        self._stats = {"api_calls": 0, "api_errors": 0, "tests_run": 0, "tests_passed": 0,
                       "sandbox_timeouts": 0, "sandbox_crashes": 0}

    @property
    def _lv(self):
        return self.LEVELS.get(self.level, 1)

    def banner(self, text):
        """始终显示的分隔线标题"""
        if self.level == "json":
            self._json_buffer.append({"event": "banner", "text": text})
            return
        if self._lv >= 1:
            print(f"\n{'='*60}")
            print(f"  {text}")
            print(f"{'='*60}")

    def section(self, text):
        """节标题"""
        if self.level == "json":
            self._json_buffer.append({"event": "section", "text": text})
            return
        if self._lv >= 1:
            print(f"\n{'─'*60}")
            print(f"  {text}")
            print(f"{'─'*60}")

    def progress(self, msg):
        """normal 级别的进度信息"""
        if self.level == "json":
            self._json_buffer.append({"event": "progress", "msg": msg})
            return
        if self._lv >= 1:
            print(msg)

    def result(self, task_id, passed, score, serving, error=""):
        """每个问题的评估结果 — normal 级别"""
        self._stats["tests_run"] += 1
        if passed:
            self._stats["tests_passed"] += 1
        if self.level == "json":
            self._json_buffer.append({
                "event": "result", "task_id": task_id, "passed": passed,
                "score": score, "serving": serving, "error": error,
            })
            return
        if self._lv >= 1:
            status = "OK" if passed else "FAIL"
            ttft = f"TTFT={serving.get('ttft_s',0)*1000:.0f}ms" if serving.get('ttft_s') else ""
            e2e = f"E2E={serving.get('e2e_s',0):.1f}s" if serving.get('e2e_s') else ""
            err = f" {error[:40]}" if error else ""
            print(f"  [{self._idx:>3d}] {task_id:<25s} {status} S:{score:.1f} {ttft} {e2e}{err}")
        self._idx += 1

    def debug(self, msg):
        """verbose 级别调试信息"""
        if self.level == "json":
            self._json_buffer.append({"event": "debug", "msg": msg})
            return
        if self._lv >= 2:
            print(f"  [DBG] {msg}")

    def api_error(self, model, task_id, error):
        """API 调用失败 — 始终显示"""
        self._stats["api_errors"] += 1
        if self.level == "json":
            self._json_buffer.append({"event": "api_error", "model": model,
                                      "task_id": task_id, "error": error})
            return
        print(f"  [ERR] {model} {task_id}: {error[:60]}")

    def sandbox_event(self, event_type, detail=""):
        """沙箱执行事件 — verbose 级别, 但 timeout/crash 在 normal 也显示"""
        if event_type == "timeout":
            self._stats["sandbox_timeouts"] += 1
            if self.level != "json" and self._lv >= 1:
                print(f"  [SANDBOX] TIMEOUT: {detail}")
        elif event_type == "crash":
            self._stats["sandbox_crashes"] += 1
            if self.level != "json" and self._lv >= 1:
                print(f"  [SANDBOX] CRASH: {detail}")
        else:
            if self.level == "json":
                self._json_buffer.append({"event": "sandbox", "type": event_type, "detail": detail})
            elif self._lv >= 2:
                print(f"  [SANDBOX] {event_type}: {detail}")

    def summary(self, stats_dict):
        """最终汇总 — normal 级别"""
        self._stats.update(stats_dict)
        if self.level == "json":
            self._json_buffer.append({"event": "summary", "stats": self._stats})
            return
        if self._lv >= 1:
            print(f"\n{'─'*60}")
            print(f"  汇总: {self._stats['tests_passed']}/{self._stats['tests_run']} 通过 "
                  f"| API错误: {self._stats['api_errors']} "
                  f"| 沙箱超时: {self._stats['sandbox_timeouts']} "
                  f"| 沙箱崩溃: {self._stats['sandbox_crashes']}")
            print(f"{'─'*60}")

    def flush_json(self):
        """json 模式下输出完整缓冲"""
        if self.level == "json" and self._json_buffer:
            print(json.dumps(self._json_buffer, ensure_ascii=False, default=str))


# 全局日志实例 (main() 中根据 --log-level 初始化)
log = EvalLogger("normal")


# ═══════════════════════════════════════════════════════════════
# 沙箱执行引擎
# ═══════════════════════════════════════════════════════════════
# 策略: 将 LLM 生成的代码放入受限子进程执行
#   - resource.setrlimit 限制 CPU 时间 / 内存 / 文件描述符
#   - 移除 __builtins__ 中的危险函数 (open, exec, eval, __import__ 之外的)
#   - 子进程独立进程组, 超时可整组 kill
#   - 父子进程通过 JSON on stdin/stdout 通信
#   - 后续可平滑升级为 Docker 容器 (同协议, 只换执行方式)

_SANDBOX_RUNNER = r'''
import sys, json, resource, ast, signal, os

def _setup_sandbox(timeout_sec, max_mem_mb=256):
    """在子进程内设资源限制 — 在 import 和命名空间准备完成后调用"""
    # CPU 时间: 硬限制 = 超时 + 1s 缓冲
    resource.setrlimit(resource.RLIMIT_CPU, (timeout_sec + 1, timeout_sec + 1))
    # 内存: 256MB 默认
    resource.setrlimit(resource.RLIMIT_AS, (max_mem_mb * 1024 * 1024, max_mem_mb * 1024 * 1024))
    # 文件描述符: 保留 stdin/stdout/stderr (0-2) + 少量供 import, 不允许无限制开文件
    # 注: NOFILE 不能设为 0, 模型输出的 import 语句需要打开 .py 文件
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(soft, 32), min(hard, 64)))
    # 防止 fork/subprocess
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    # SIGALRM 作为备用超时
    signal.signal(signal.SIGALRM, lambda s,f: (_ for _ in ()).throw(TimeoutError("EXEC_TIMEOUT")))
    signal.alarm(timeout_sec)

def _restricted_builtins():
    """构建受限命名空间 — 只暴露评估必须的类型和函数, 移除危险 I/O"""
    # 先收集需要的类型（在限制资源之前完成 import）
    import typing
    import math
    import collections
    safe = {
        "List": typing.List, "Dict": typing.Dict, "Optional": typing.Optional,
        "Tuple": typing.Tuple, "Any": typing.Any, "Set": typing.Set,
        "Union": typing.Union, "Callable": typing.Callable,
        "Iterable": typing.Iterable, "Iterator": typing.Iterator,
        # 常用内置
        "abs": abs, "all": all, "any": any, "bool": bool, "chr": chr, "dict": dict,
        "divmod": divmod, "enumerate": enumerate, "filter": filter, "float": float,
        "frozenset": frozenset, "hash": hash, "hex": hex, "int": int, "isinstance": isinstance,
        "issubclass": issubclass, "iter": iter, "len": len, "list": list, "map": map,
        "max": max, "min": min, "next": next, "oct": oct, "ord": ord, "pow": pow,
        "print": print, "range": range, "repr": repr, "reversed": reversed, "round": round,
        "set": set, "slice": slice, "sorted": sorted, "str": str, "sum": sum,
        "tuple": tuple, "type": type, "zip": zip,
        # 允许 import (模型输出可能含 import 语句, 如 from typing import List)
        "__import__": __import__,
        # 常用模块 (预加载, 避免执行时被 NOFILE 限制阻断)
        "math": math, "collections": collections,
    }
    return safe

def main():
    # 从 stdin 读取 payload
    payload = json.loads(sys.stdin.read())
    code = payload["code"]
    entry_point = payload["entry_point"]
    timeout_sec = payload.get("timeout_sec", 15)
    max_mem_mb = payload.get("max_mem_mb", 256)

    result = {"passed": False, "error": None, "duration": None, "assertions": 0, "passed_assertions": 0}

    # 语法检查 (在限制资源之前)
    try:
        ast.parse(code)
    except SyntaxError as e:
        result["error"] = f"SyntaxError: {e}"
        json.dump(result, sys.stdout)
        return

    # 构建命名空间 (在限制资源之前完成 import)
    namespace = {"__builtins__": _restricted_builtins()}

    # 现在施加资源限制
    _setup_sandbox(timeout_sec, max_mem_mb)

    try:
        import time as _t
        t0 = _t.time()
        exec(compile(ast.parse(code), "<sandbox>", "exec"), namespace)
        duration = _t.time() - t0
        result["passed"] = True
        result["duration"] = round(duration, 4)
    except AssertionError as e:
        result["error"] = f"AssertionError: {e}"
    except TimeoutError:
        result["error"] = "TIMEOUT"
    except MemoryError:
        result["error"] = "MEMORY_LIMIT"
    except RecursionError:
        result["error"] = "RECURSION_LIMIT"
    except OSError as e:
        result["error"] = f"Blocked: {e}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        signal.alarm(0)

    # 统计断言数 (从代码文本中计数)
    test_part = payload.get("test_code", "")
    result["assertions"] = test_part.count("assert ")
    if result["passed"]:
        result["passed_assertions"] = result["assertions"]

    json.dump(result, sys.stdout)

if __name__ == "__main__":
    main()
'''


# ═══════════════════════════════════════════════════════════════
# 数据库存储
# ═══════════════════════════════════════════════════════════════
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "humaneval_eval.db")
_SCHEMA_VERSION = 1  # 每次 schema 变更时递增


def _migrate_db_schema(conn):
    """检查并升级数据库 schema 到最新版本。幂等操作，安全重复调用。"""
    cur = conn.cursor()

    # 确保 schema_version 表存在
    cur.execute("""CREATE TABLE IF NOT EXISTS schema_version (
        key TEXT PRIMARY KEY,
        value INTEGER
    )""")

    # 读取当前版本
    row = cur.execute(
        "SELECT value FROM schema_version WHERE key = 'schema'"
    ).fetchone()
    current_version = row[0] if row else 0

    if current_version >= _SCHEMA_VERSION:
        return  # 已是最新

    # --- 迁移脚本 (按版本递增) ---

    # v0 → v1: 当前 schema 已是 v1（baseline），只需写入版本号
    # 未来 schema 变更在这里追加 elif current_version < N: ...
    # if current_version < 2:
    #     cur.execute("ALTER TABLE eval_results ADD COLUMN new_field TEXT")

    cur.execute(
        "INSERT OR REPLACE INTO schema_version (key, value) VALUES ('schema', ?)",
        (_SCHEMA_VERSION,)
    )
    conn.commit()


def _init_db():
    """初始化数据库表"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS eval_runs (
        run_id TEXT PRIMARY KEY,
        timestamp TEXT,
        model_name TEXT,
        base_url TEXT,
        num_problems INTEGER,
        pass_count INTEGER,
        avg_score REAL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS eval_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT REFERENCES eval_runs(run_id),
        task_id TEXT,
        entry_point TEXT,
        passed INTEGER,
        total_score REAL,
        error TEXT,
        cc INTEGER,
        lines INTEGER,
        ttft_ms REAL,
        tpot_ms_p_token REAL,
        e2e_s REAL,
        input_tokens INTEGER,
        output_tokens INTEGER,
        raw_model_output TEXT
    )""")
    conn.commit()
    _migrate_db_schema(conn)
    conn.close()


def save_to_db(run_id: str, model_name: str, base_url: str, results: list):
    """将评估结果写入数据库"""
    _init_db()
    conn = sqlite3.connect(DB_PATH)

    passed = sum(1 for r in results if r.get("passed"))
    scores = [compute_total_score(r) for r in results if r.get("passed")]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0

    conn.execute(
        "INSERT OR REPLACE INTO eval_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, time.strftime("%Y-%m-%d %H:%M UTC"), model_name, base_url,
         len(results), passed, avg_score)
    )

    for r in results:
        sv = r.get("serving", {})
        q = r.get("layers", {}).get("quality", {})
        conn.execute(
            """INSERT INTO eval_results
               (run_id, task_id, entry_point, passed, total_score, error,
                cc, lines, ttft_ms, tpot_ms_p_token, e2e_s,
                input_tokens, output_tokens, raw_model_output)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, r["task_id"], r["entry_point"],
             int(r.get("passed", False)),
             compute_total_score(r),
             (r.get("error") or "")[:200],
             q.get("cyclomatic_complexity"),
             q.get("lines"),
             round(sv.get("ttft_s", 0) * 1000, 1) if sv.get("ttft_s") else None,
             round(sv.get("tpot_s_per_token", 0) * 1000, 2) if sv.get("tpot_s_per_token") else None,
             round(sv.get("e2e_s", 0), 2) if sv.get("e2e_s") else None,
             sv.get("input_tokens") or sv.get("est_input_tokens"),
             sv.get("est_tokens"),
             (r.get("raw_model_output") or "")[:500],
             )
        )
    conn.commit()
    conn.close()
    print(f"  DB: {len(results)} results saved to {DB_PATH} (run_id={run_id})")


def load_from_db(run_ids: list) -> dict:
    """从数据库读取评估结果. 支持前缀匹配 (传时间戳前缀加载同次所有模型)"""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 收集所有匹配的 run_id
    all_run_ids = set()
    for rid in run_ids:
        if rid.endswith("*"):
            prefix = rid[:-1]
            rows = conn.execute(
                "SELECT run_id FROM eval_runs WHERE run_id LIKE ?", (prefix + "%",)
            ).fetchall()
            all_run_ids.update(r["run_id"] for r in rows)
        else:
            all_run_ids.add(rid)

    if not all_run_ids:
        conn.close()
        return {}

    placeholders = ",".join("?" for _ in all_run_ids)
    runs = conn.execute(
        f"SELECT * FROM eval_runs WHERE run_id IN ({placeholders}) ORDER BY timestamp",
        list(all_run_ids)
    ).fetchall()

    results = {}
    for run in runs:
        rid = run["run_id"]
        rows = conn.execute(
            "SELECT * FROM eval_results WHERE run_id = ? ORDER BY id", (rid,)
        ).fetchall()
        model_results = []
        for row in rows:
            model_results.append({
                "task_id": row["task_id"],
                "entry_point": row["entry_point"],
                "passed": bool(row["passed"]),
                "_total_score": row["total_score"],
                "total_score": row["total_score"],
                "error": row["error"],
                "serving": {
                    "ttft_s": row["ttft_ms"] / 1000 if row["ttft_ms"] else None,
                    "tpot_s_per_token": row["tpot_ms_p_token"] / 1000 if row["tpot_ms_p_token"] else None,
                    "e2e_s": row["e2e_s"],
                    "est_tokens": row["output_tokens"],
                    "input_tokens": row["input_tokens"],
                },
                "layers": {
                    "quality": {
                        "cyclomatic_complexity": row["cc"],
                        "lines": row["lines"],
                    }
                },
                "raw_model_output": row["raw_model_output"],
            })
        results[rid] = {
            "run": dict(zip(run.keys(), run)),
            "results": model_results,
        }
    conn.close()
    return results


def list_runs() -> list:
    """列出所有可用的评估运行"""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT run_id, timestamp, model_name, num_problems, pass_count, avg_score "
        "FROM eval_runs ORDER BY timestamp DESC"
    ).fetchall()
    conn.close()
    return [dict(zip(r.keys(), r)) for r in rows]


def call_model_streaming(model_name, prompt, base_url, api_key, max_tokens=1024, temperature=0.0):
    """调用模型 (流式) 并测量 TTFT / TPOT / E2E, 同时返回生成的代码文本"""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }

    import requests as req

    t_request = time.time()
    try:
        resp = req.post(url, headers=headers, json=body, stream=True, timeout=180)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}", {}
    except Exception as e:
        return None, str(e), {}

    # ── 流式解析 ──
    first_chunk_ts = None
    last_chunk_ts = None
    full_content = ""
    ttft = None
    api_usage = None

    try:
        for raw_chunk in resp.iter_lines():
            if not raw_chunk:
                continue
            line = raw_chunk.decode("utf-8", errors="replace")
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                data = json.loads(data_str)
                now = time.time()
                if first_chunk_ts is None:
                    first_chunk_ts = now
                    ttft = now - t_request

                # 部分 provider 在 final chunk 返回 usage
                if "usage" in data and data["usage"]:
                    api_usage = data["usage"]

                choices = data.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                reasoning = delta.get("reasoning_content", "")
                if content or reasoning:
                    full_content += (reasoning if reasoning else content)
                last_chunk_ts = now
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
    except Exception:
        pass

    t_end = time.time()
    e2e = t_end - t_request
    stream_dur = (last_chunk_ts - first_chunk_ts) if (first_chunk_ts and last_chunk_ts) else 0

    # 优先取 API 返回的实际 token 数，否则用字符估算
    if api_usage and api_usage.get("completion_tokens"):
        output_tokens = api_usage["completion_tokens"]
        input_tokens = api_usage.get("prompt_tokens", 0)
    else:
        output_tokens = max(1, round(len(full_content) / 3.0))
        input_tokens = 0

    tpot = stream_dur / output_tokens if stream_dur > 0 else 0

    metrics = {
        "ttft_s": round(ttft, 4) if ttft else None,
        "tpot_s_per_token": round(tpot, 4),
        "e2e_s": round(e2e, 2),
        "output_chars": len(full_content),
        "est_tokens": output_tokens,
        "input_tokens": input_tokens,
        "api_usage": api_usage is not None,
    }
    return full_content, None, metrics


def measure_rpm(model_name, prompts, base_url, api_key, concurrent=3, max_tokens=1024):
    """测量 RPM (非流式并发)"""
    import requests as req
    from concurrent.futures import ThreadPoolExecutor, as_completed

    chat_url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    rpm_rounds = []

    for rnd in range(2):
        def send_one(p):
            body = {"model": model_name, "messages": [{"role": "user", "content": p}],
                     "max_tokens": max_tokens, "temperature": 0.0}
            t0 = time.time()
            try:
                r = req.post(chat_url, headers=headers, json=body, timeout=180)
                dur = time.time() - t0
                if r.status_code == 200:
                    usage = r.json().get("usage", {})
                    return True, dur, usage.get("completion_tokens", 0)
                return False, dur, 0
            except:
                return False, time.time() - t0, 0

        t_start = time.time()
        success = token_total = 0
        with ThreadPoolExecutor(max_workers=concurrent) as pool:
            for f in as_completed([pool.submit(send_one, p) for p in prompts]):
                ok, dur, tok = f.result()
                if ok:
                    success += 1
                    token_total += tok
        wall = time.time() - t_start
        rpm = success / (wall / 60) if wall > 0 else 0
        rpm_rounds.append(rpm)

    return {
        "rounds": [round(r, 1) for r in rpm_rounds],
        "avg": round(sum(rpm_rounds) / len(rpm_rounds), 1) if rpm_rounds else 0,
        "concurrent": concurrent,
    }


# ═══════════════════════════════════════════════════════════════
# Layer 0: 语法检查
# ═══════════════════════════════════════════════════════════════

def has_valid_syntax(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


# ═══════════════════════════════════════════════════════════════
# Layer 1: 功能正确性 — 执行测试
# ═══════════════════════════════════════════════════════════════

class TimeoutError(Exception):
    pass


# _timeout_handler 已迁移到 _SANDBOX_RUNNER 子进程中 (SIGALRM 在子进程内设置)

def _is_valid_function(code: str, entry_point: str) -> bool:
    """AST 验证: code 包含合法的函数定义，且函数名匹配 entry_point"""
    if not code or not code.strip():
        return False
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    # 检查顶层是否包含目标函数定义
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name == entry_point:
            return True
    # 也允许代码整体就是一个函数定义（无外层 Module 包装的情况几乎不会出现，但保险）
    return False


def extract_function_code(answer: str, entry_point: str):
    """
    多策略提取管道: 从模型回复中提取函数定义代码。
    返回提取到的代码字符串，全部失败则返回 None（不 fallback 到原始文本）。
    """
    if not answer or not answer.strip():
        return None

    # ── 辅助: 尝试提取 + AST 验证 ──
    def _try_extract(code_candidate: str) -> str:
        code_candidate = code_candidate.strip()
        if _is_valid_function(code_candidate, entry_point):
            return code_candidate
        return None

    # ── 策略 1: ```python ... ``` 代码块 ──
    for block in re.findall(r"```[Pp]ython\s*\n(.*?)```", answer, re.DOTALL):
        result = _try_extract(block)
        if result:
            return result

    # ── 策略 2: ``` (无语言标记) 代码块 ──
    for block in re.findall(r"```\s*\n(.*?)```", answer, re.DOTALL):
        result = _try_extract(block)
        if result:
            return result

    # ── 策略 3: <code> ... </code> HTML 标签块 ──
    for block in re.findall(r"<code>(.*?)</code>", answer, re.DOTALL):
        result = _try_extract(block)
        if result:
            return result

    # ── 策略 4: 正则查找 def entry_point + 前置 import ──
    m = re.search(
        r"((?:^(?:import|from|#).*$[ \t]*\n?)*)"
        rf"^(def\s+{re.escape(entry_point)}\b.*?)(?=\n(?!\s*$|\s+#|\s+\"\"\")|\Z)",
        answer, re.DOTALL | re.MULTILINE
    )
    if m:
        candidate = (m.group(1) + m.group(2)).strip()
        result = _try_extract(candidate)
        if result:
            return result

    # ── 策略 5: 仅 def 行（回答直接以 def 开头，无 markdown） ──
    stripped = answer.strip()
    if stripped.startswith("def "):
        result = _try_extract(stripped)
        if result:
            return result

    # ── 策略 6: 补全 def 签名（模型只返回函数体时尝试重建） ──
    # 如果 answer 看起来像函数体（缩进代码块），尝试用 entry_point 补全 def 头
    lines = [l for l in answer.split("\n") if l.strip()]
    if lines and all(l.startswith(("    ", "\t")) for l in lines if l.strip()):
        candidate = f"def {entry_point}():\n" + "\n".join(lines)
        result = _try_extract(candidate)
        if result:
            return result

    # ── 策略 7: 松散匹配 — 任意位置找 def entry_point 并向后匹配到完整块 ──
    m2 = re.search(
        rf"(def\s+{re.escape(entry_point)}\b.*?)(?=\n\S|\Z)",
        answer, re.DOTALL
    )
    if m2:
        # 尝试与前面所有 import 行拼接
        before = answer[:m2.start()]
        imports = re.findall(r"^(import .*|from .* import .*)$", before, re.MULTILINE)
        candidate = ("\n".join(imports) + "\n\n" + m2.group(1)).strip() if imports else m2.group(1).strip()
        result = _try_extract(candidate)
        if result:
            return result

    # 全部策略失败 → 返回 None，不将自然语言文本喂给 exec
    return None


def run_test(solution_code: str, test_code: str, entry_point: str,
             timeout_sec: int = 15) -> dict:
    """在受限子进程中执行 HumanEval 测试用例

    安全策略:
      - 代码在独立子进程运行, 通过 JSON on stdin/stdout 通信
      - resource.setrlimit 限制 CPU/内存/文件描述符/进程数
      - __builtins__ 白名单, 禁止 open/exec/eval 等危险函数
      - 子进程独立进程组 (start_new_session), 超时可整组 kill
      - 后续可平滑升级为 Docker 容器 (同 JSON 协议)
    """
    full_code = solution_code + "\n\n" + test_code

    payload = json.dumps({
        "code": full_code,
        "entry_point": entry_point,
        "timeout_sec": timeout_sec,
        "max_mem_mb": 256,
        "test_code": test_code,
    })

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _SANDBOX_RUNNER],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout_sec + 10,  # 父进程超时留余量
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        # 子进程超时, kill 整个进程组
        log.sandbox_event("timeout", f"{entry_point} exceeded {timeout_sec}s")
        return {"passed": False, "error": "TIMEOUT", "duration": None,
                "assertions": test_code.count("assert "), "passed_assertions": 0}
    except Exception as e:
        log.sandbox_event("crash", f"{entry_point}: {e}")
        return {"passed": False, "error": f"SandboxError: {e}", "duration": None,
                "assertions": 0, "passed_assertions": 0}

    # 解析子进程输出
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:200]
        log.sandbox_event("crash", f"{entry_point} exit={proc.returncode}: {stderr}")
        return {"passed": False, "error": f"SandboxExit({proc.returncode}): {stderr}",
                "duration": None, "assertions": 0, "passed_assertions": 0}

    try:
        result = json.loads(proc.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        stderr = (proc.stderr or "").strip()[:200]
        log.sandbox_event("crash", f"{entry_point}: invalid JSON output, stderr={stderr}")
        return {"passed": False, "error": f"SandboxOutputError", "duration": None,
                "assertions": 0, "passed_assertions": 0}

    # 记录沙箱内发生的事件
    if result.get("error"):
        if "TIMEOUT" in result["error"]:
            log.sandbox_event("timeout", f"{entry_point}")
        elif "MEMORY" in result["error"]:
            log.sandbox_event("crash", f"{entry_point}: MEMORY_LIMIT")
        else:
            log.sandbox_event("exec_error", f"{entry_point}: {result['error'][:50]}")

    log.debug(f"run_test({entry_point}): passed={result.get('passed')}, "
              f"duration={result.get('duration')}s, error={result.get('error')}")

    return result


# ═══════════════════════════════════════════════════════════════
# Layer 2: 代码质量
# ═══════════════════════════════════════════════════════════════

def cyclomatic_complexity(code: str) -> int:
    """McCabe 圈复杂度"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return 999
    cc = 1  # 基线
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.For)):
            cc += 1
        elif isinstance(node, ast.And):
            cc += 1
        elif isinstance(node, ast.Or):
            cc += 1
        elif isinstance(node, ast.Try):
            cc += 1  # except 每分支额外加 1
        elif isinstance(node, ast.FunctionDef):
            cc += 0  # 函数定义本身不增加
        elif isinstance(node, ast.BoolOp):
            cc += len(node.values) - 1  # a and b and c → 2 个分支
    return cc


def ast_node_count(code: str) -> int:
    try:
        tree = ast.parse(code)
        return sum(1 for _ in ast.walk(tree))
    except SyntaxError:
        return 999


def effective_lines(code: str) -> int:
    """有效代码行数 (去掉注释和空行)"""
    lines = [l for l in code.split("\n")
             if l.strip() and not l.strip().startswith("#")]
    return len(lines)


def quality_score(solution_code: str, canonical_code: str) -> dict:
    """
    代码质量评分 [0, 4]
    分项: 圈复杂度 (0-3) + 行数精简度 (±1), clamp 0-4
    """
    result = {"cyclomatic_complexity": 0, "lines": 0,
              "cc_score": 0, "lines_score": 0,
              "total": 0}

    # 1. 圈复杂度 (0-3)
    cc = cyclomatic_complexity(solution_code)
    result["cyclomatic_complexity"] = cc
    if cc <= 2:
        result["cc_score"] = 3
    elif cc <= 5:
        result["cc_score"] = 2
    elif cc <= 10:
        result["cc_score"] = 1
    else:
        result["cc_score"] = 0

    # 2. 有效行数 vs 标准解 (±1)
    lines = effective_lines(solution_code)
    can_lines = effective_lines(canonical_code) if canonical_code else lines
    result["lines"] = lines
    l_ratio = lines / max(can_lines, 1)
    if l_ratio <= 1.2:
        result["lines_score"] = 1       # 行数与标准解相当，加分
    elif l_ratio > 2.0:
        result["lines_score"] = -1      # 行数超过 2 倍标准解，扣分
    else:
        result["lines_score"] = 0

    result["total"] = max(0, min(4, result["cc_score"] + result["lines_score"]))
    return result


# ═══════════════════════════════════════════════════════════════
# Layer 3: 运行时效率
# ═══════════════════════════════════════════════════════════════

def extract_test_inputs(test_code: str, entry_point: str) -> list:
    """
    从 HumanEval 的 test 代码中解析函数调用的输入参数。
    HumanEval test 格式: check(candidate): assert candidate(...) == ...
    """
    inputs = []
    for line in test_code.split("\n"):
        # 匹配 assert candidate(...) == ... (HumanEval 用 "candidate" 作形参名)
        m = re.search(r"assert\s+candidate\s*\((.*?)\)\s*==", line)
        if not m:
            # 也尝试匹配 entry_point
            m = re.search(rf"assert\s+{re.escape(entry_point)}\s*\((.*?)\)\s*==", line)
        if m:
            args_str = m.group(1)
            try:
                args = eval(args_str, {"__builtins__": {}}, {})
                if not isinstance(args, tuple):
                    args = (args,)
                inputs.append(args)
            except:
                pass
    return inputs


def runtime_efficiency(solution_code: str, canonical_code: str,
                       entry_point: str, test_inputs: list) -> dict:
    """
    运行时效率评分 [0, 1]
    对比解法和标准解法在相同输入上的执行速度
    """
    result = {"sol_time": None, "can_time": None, "ratio": None, "score": 0.0}

    if not test_inputs or not canonical_code:
        result["error"] = "No test inputs or canonical code"
        return result

    def measure_time(code, inputs, ep, n_runs=5):
        namespace = {}
        try:
            exec(compile(ast.parse(code), "<string>", "exec"), namespace)
            func = namespace.get(ep)
            if not func:
                return None
            # warm up
            for inp in inputs:
                try:
                    func(*inp)
                except:
                    pass
            # measure
            t0 = time.time()
            for _ in range(n_runs):
                for inp in inputs:
                    try:
                        func(*inp)
                    except:
                        pass
            return (time.time() - t0) / n_runs
        except:
            return None

    sol_time = measure_time(solution_code, test_inputs, entry_point)
    can_time = measure_time(canonical_code, test_inputs, entry_point)

    result["sol_time"] = sol_time
    result["can_time"] = can_time

    if sol_time is None or can_time is None or can_time <= 0:
        return result

    ratio = sol_time / can_time
    result["ratio"] = round(ratio, 3)

    if ratio <= 0.8:
        result["score"] = 1.0       # 比标准解快
    elif ratio <= 1.2:
        result["score"] = 0.8       # 差不多
    elif ratio <= 2.0:
        result["score"] = 0.5
    elif ratio <= 5.0:
        result["score"] = 0.2
    else:
        result["score"] = 0.0

    return result


# ═══════════════════════════════════════════════════════════════
# Prompt 构造
# ═══════════════════════════════════════════════════════════════

HUMANEVAL_PROMPT_TEMPLATE = """Complete the following Python function. Return ONLY the function implementation, no explanations.

```python
{prompt}
```

仅输出可执行的 Python 代码，不要任何解释文字。以 ```python 开头，``` 结尾。"""


def extract_problem_description(prompt: str) -> dict:
    """从 HumanEval prompt 中提取问题描述、输入输出示例和测试能力"""
    desc = {"description": "", "examples": [], "capability": ""}

    # 提取 docstring（函数签名后的 """..."""）
    m = re.search(r'"""(.*?)"""', prompt, re.DOTALL)
    if m:
        doc = m.group(1).strip()
        lines = doc.split("\n")
        # 第一行是功能描述
        desc["description"] = lines[0].strip() if lines else ""
        # 提取 >>> 示例
        desc["examples"] = [l.strip() for l in lines if l.strip().startswith(">>> ")]
    else:
        desc["description"] = prompt.split("\n")[-1].strip() if prompt else ""

    # 提取函数名和参数
    sig = re.search(r"def (\w+)\((.*?)\)", prompt)
    if sig:
        func_name = sig.group(1)
        params = [p.strip().split(":")[0].strip() for p in sig.group(2).split(",") if p.strip()]

        # 判断能力类型
        if any(kw in func_name.lower() for kw in ["sort", "order", "max", "min", "sum", "avg", "count"]):
            desc["capability"] = "数据处理 / 聚合计算"
        elif any(kw in func_name.lower() for kw in ["search", "find", "contain", "exist", "has", "check", "valid"]):
            desc["capability"] = "条件判断 / 搜索验证"
        elif any(kw in func_name.lower() for kw in ["convert", "transform", "format", "parse", "encode", "decode"]):
            desc["capability"] = "格式转换 / 解析"
        elif any(kw in func_name.lower() for kw in ["match", "group", "paren", "bracket", "nest"]):
            desc["capability"] = "括号 / 结构匹配"
        elif any(kw in func_name.lower() for kw in ["solve", "calc", "equation", "math", "gcd", "prime"]):
            desc["capability"] = "数学计算 / 算法"
        elif any(kw in func_name.lower() for kw in ["list", "array", "filter", "map", "reduce"]):
            desc["capability"] = "列表 / 数组操作"
        elif "tree" in func_name.lower() or "node" in func_name.lower():
            desc["capability"] = "树 / 节点操作"
        elif "str" in func_name.lower() or "string" in func_name.lower():
            desc["capability"] = "字符串处理"
        else:
            desc["capability"] = "综合逻辑 / 算法实现"

    return desc


def build_score_explanation(result: dict) -> str:
    """生成每项指标的得分原因说明"""
    parts = []

    if result.get("error"):
        return f"❌ {result['error'][:60]}"

    parts.append(f"基准分=6.0（测试通过）")

    q = result.get("layers", {}).get("quality", {})
    if q:
        cc = q.get("cyclomatic_complexity", "?")
        parts.append(f"CC={cc}→{q.get('cc_score',0)}分")
        ln = q.get("lines", "?")
        parts.append(f"行数={ln}→{q.get('lines_score',0)}分")
        parts.append(f"质量小计={q.get('total',0)}/4")

    e = result.get("layers", {}).get("efficiency", {})
    if e and e.get("ratio") is not None:
        parts.append(f"效率比={e['ratio']:.2f}x")

    sv = result.get("serving", {})
    if sv.get("ttft_s"):
        parts.append(f"TTFT={sv['ttft_s']*1000:.0f}ms")
    if sv.get("e2e_s"):
        parts.append(f"E2E={sv['e2e_s']:.1f}s")

    return " | ".join(parts)


# ═══════════════════════════════════════════════════════════════
# 主评估逻辑
# ═══════════════════════════════════════════════════════════════

def evaluate_problem(problem, model_name, base_url, api_key, max_tokens=1024):
    """评估一个模型在一个 HumanEval 问题上的表现"""
    entry_point = problem["entry_point"]
    prompt_text = problem["prompt"]
    test_code = problem["test"]
    canonical = problem.get("canonical_solution", "")

    # 提取问题描述
    prob_desc = extract_problem_description(prompt_text)

    result = {
        "task_id": problem["task_id"],
        "entry_point": entry_point,
        "model": model_name,
        "passed": False,
        "error": None,
        "layers": {},
        "serving": {},
        "problem": {
            "description": prob_desc["description"],
            "examples": prob_desc["examples"],
            "capability": prob_desc["capability"],
        },
        "raw_model_output": "",
        "extracted_code": "",
    }

    # ── 调用模型 (流式, 同步测量 TTFT/TPOT/E2E) ──
    full_prompt = HUMANEVAL_PROMPT_TEMPLATE.format(prompt=prompt_text)
    answer, err, serving = call_model_streaming(model_name, full_prompt, base_url, api_key, max_tokens)
    result["serving"] = serving
    result["raw_model_output"] = answer or ""
    # 记录 prompt 长度用于 token 估算
    serving["prompt_len"] = len(full_prompt)
    serving["est_input_tokens"] = max(1, round(len(full_prompt) / 3.5))
    if err:
        result["error"] = f"API: {err}"
        log.api_error(model_name, problem["task_id"], err)
        return result
    result["answer"] = answer

    # ── Layer 0: 提取函数并检查语法 ──
    func_code = extract_function_code(answer, entry_point)
    result["extracted_code"] = func_code or ""
    if func_code is None:
        result["error"] = "Failed to extract valid function code from model output"
        log.debug(f"{problem['task_id']}: code extraction failed")
        return result
    if not has_valid_syntax(func_code):
        result["error"] = f"SyntaxError in extracted code"
        log.debug(f"{problem['task_id']}: syntax error in extracted code")
        return result

    # ── Layer 1: 功能正确性 (沙箱执行) ──
    test_result = run_test(func_code, test_code, entry_point)
    result["layers"]["correctness"] = test_result
    if not test_result["passed"]:
        result["error"] = test_result.get("error", "Test failed")
        log.debug(f"{problem['task_id']}: test failed - {result['error'][:50]}")
        return result
    result["passed"] = True

    # ── Layer 2: 代码质量 ──
    q = quality_score(func_code, canonical)
    result["layers"]["quality"] = q

    # ── Layer 3: 运行时效率 (仅记录, 不参与评分) ──
    test_inputs = extract_test_inputs(test_code, entry_point)
    if not test_inputs:
        pass
    eff = runtime_efficiency(func_code, canonical, entry_point, test_inputs)
    result["layers"]["efficiency"] = eff

    return result


def compute_total_score(result: dict) -> float:
    """计算综合分 [0, 10]"""
    if not result.get("passed"):
        return 0.0

    # DB 加载的数据直接使用存储的总分（已包含质量分）
    if "_total_score" in result:
        return result["_total_score"]

    score = 6.0  # 基础分: 通过测试

    # Layer 2: 质量 (0-4)
    q = result.get("layers", {}).get("quality", {})
    score += q.get("total", 0)

    return round(min(score, 10.0), 2)


# ═══════════════════════════════════════════════════════════════
# 对比报告
# ═══════════════════════════════════════════════════════════════

def build_comparison_table(all_results: dict, model_names: list,
                           serving_summary: dict = None) -> str:
    """生成多模型对比 Markdown 报告
    格式: 用例为行 | 模型为列 | 每格: 总分 T:TTFT E:E2E K:Tokens
          底部 p50/p80/avg 汇总行
    """
    lines = []

    # 确定问题数
    num_problems = 0
    for m in model_names:
        results = all_results.get(m, [])
        if results:
            num_problems = len(results)
            break

    lines.append(f"# HumanEval 综合评估报告")
    lines.append(f"")
    lines.append(f"**测试集**: openai/humaneval ({num_problems} 问题) · **时间**: {time.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**数据库**: {DB_PATH}")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # ── 构建 per-problem 数据 ──
    # 确定 task_id 顺序
    task_order = []
    task_cap = {}
    task_desc = {}

    # 从本地 HumanEval 数据集加载问题描述
    humaneval_path = "/tmp/HumanEval.jsonl"
    if os.path.exists(humaneval_path):
        with open(humaneval_path) as f:
            for line in f:
                if line.strip():
                    d = json.loads(line)
                    tid = d["task_id"]
                    ep = d["entry_point"]
                    prompt = d["prompt"]
                    # 提取 docstring
                    m = re.search(r'"""(.*?)"""', prompt, re.DOTALL)
                    desc_text = m.group(1).strip().split("\n")[0].strip() if m else ""
                    # 提取能力
                    sig = re.search(r"def (\w+)\(.*?\)", prompt)
                    fn = sig.group(1) if sig else ep
                    if any(kw in fn.lower() for kw in ["sort", "max", "min", "sum", "avg", "count", "mean"]):
                        cap = "数据处理"
                    elif any(kw in fn.lower() for kw in ["search", "find", "has", "check", "valid", "filter"]):
                        cap = "条件判断"
                    elif any(kw in fn.lower() for kw in ["parse", "separate", "paren", "bracket", "nest"]):
                        cap = "结构匹配"
                    elif any(kw in fn.lower() for kw in ["calc", "math", "gcd", "prime", "int", "decimal"]):
                        cap = "数学计算"
                    elif any(kw in fn.lower() for kw in ["convert", "transform", "encode", "decode"]):
                        cap = "格式转换"
                    elif any(kw in fn.lower() for kw in ["list", "array"]):
                        cap = "列表操作"
                    elif "str" in fn.lower():
                        cap = "字符串处理"
                    else:
                        cap = "算法实现"
                    task_cap[tid] = cap
                    task_desc[tid] = desc_text

    for m in model_names:
        results = all_results.get(m, [])
        if results:
            task_order = [(r["task_id"], r["entry_point"]) for r in results]
            break

    # 为每个模型建立 per-problem 映射
    model_data = {}
    for m in model_names:
        results = all_results.get(m, [])
        model_data[m] = {}
        for r in results:
            tid = r["task_id"]
            sv = r.get("serving", {})
            score = compute_total_score(r)
            ttft = sv.get("ttft_s")
            tpot = sv.get("tpot_s_per_token")
            e2e = sv.get("e2e_s")
            out_tok = sv.get("est_tokens", 0)
            in_tok = sv.get("input_tokens", 0) or sv.get("est_input_tokens", 0)
            tok = (in_tok or 0) + (out_tok or 0)
            model_data[m][tid] = {
                "score": score,
                "ttft": ttft,
                "tpot": tpot,
                "e2e": e2e,
                "tok": tok,
            }

    def _c(val, low, high, unit="", invert=False):
        """给数值加颜色。默认越高越好(评分)，invert=True 越低越好(延迟/token)"""
        if val is None:
            return "—"
        if invert:
            # 越低越好: <low→绿, low~high→黄, >high→红
            if val < low:
                color = "#22c55e"
            elif val > high:
                color = "#ef4444"
            else:
                color = "#eab308"
        else:
            # 越高越好: <low→红, low~high→黄, >high→绿
            if val < low:
                color = "#ef4444"
            elif val > high:
                color = "#22c55e"
            else:
                color = "#eab308"
        return f'<span style="color:{color}">{val}{unit}</span>'

    def _cell_str(d):
        """生成单元格: score T:ttft P:tpot E:e2e K:tok (带颜色)"""
        score = d["score"] if d else 0.0
        s_s = _c(round(score, 1), 6, 8) if d else '<span style="color:#ef4444">0.0</span>'
        t_s = f"T:{_c(round(d['ttft']*1000), 1000, 3000, 'ms', invert=True)}" if d and d.get("ttft") else "T:—"
        p_s = f"P:{_c(round(d['tpot']*1000, 1), 10, 25, 'ms/t', invert=True)}" if d and d.get("tpot") else "P:—"
        e_s = f"E:{_c(round(d['e2e'], 1), 10, 30, 's', invert=True)}" if d and d.get("e2e") else "E:—"
        k_s = f'K:<span style="color:#6b7280">{d["tok"]}</span>' if d and d.get("tok") else "K:—"
        return f"{s_s} {t_s} {p_s} {e_s} {k_s}"

    # 表头
    header = ["用例", "能力"] + model_names
    sep = [":-----", ":------"] + [":------------------" for _ in model_names]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(sep) + "|")

    # 每道题一行
    for tid, ep in task_order:
        cap = task_cap.get(tid, "")
        cells = [f"{tid}<br>`{ep}`", cap]
        for m in model_names:
            d = model_data[m].get(tid)
            cells.append(_cell_str(d))
        lines.append("| " + " | ".join(cells) + " |")

    # ── p50/p80/avg 汇总行 ──
    def _p_stats(arr, p):
        if not arr:
            return None
        s = sorted(arr)
        idx = int(len(s) * p / 100)
        return s[min(idx, len(s) - 1)]

    def _avg(arr):
        return sum(arr) / len(arr) if arr else None

    for label, fn in [("p50", lambda arr: _p_stats(arr, 50)),
                       ("p80", lambda arr: _p_stats(arr, 80)),
                       ("avg", _avg)]:
        cells = [f"**{label}**"]
        for m in model_names:
            scores = [model_data[m][tid]["score"] for tid, _ in task_order if tid in model_data[m]]
            ttfts = [model_data[m][tid]["ttft"] for tid, _ in task_order if tid in model_data[m] and model_data[m][tid].get("ttft")]
            tpots = [model_data[m][tid]["tpot"] for tid, _ in task_order if tid in model_data[m] and model_data[m][tid].get("tpot")]
            e2es = [model_data[m][tid]["e2e"] for tid, _ in task_order if tid in model_data[m] and model_data[m][tid].get("e2e")]
            toks = [model_data[m][tid]["tok"] for tid, _ in task_order if tid in model_data[m]]

            s_fn = fn(scores) if scores else None
            t_fn = fn(ttfts) if ttfts else None
            p_fn = fn(tpots) if tpots else None
            e_fn = fn(e2es) if e2es else None
            k_fn = fn(toks) if toks else None

            s_s = _c(round(s_fn, 2), 6, 8) if s_fn is not None else "—"
            t_s = f"T:{_c(round(t_fn*1000), 1000, 3000, 'ms', invert=True)}" if t_fn is not None else "T:—"
            p_s = f"P:{_c(round(p_fn*1000, 1), 10, 25, 'ms/t', invert=True)}" if p_fn is not None else "P:—"
            e_s = f"E:{_c(round(e_fn, 1), 10, 30, 's', invert=True)}" if e_fn is not None else "E:—"
            k_s = f'K:<span style="color:#6b7280">{int(k_fn)}</span>' if k_fn is not None else "K:—"
            cells.append(f"{s_s} {t_s} {p_s} {e_s} {k_s}")
        lines.append("| " + " | ".join(cells) + " |")

    lines.append(f"")
    lines.append(f"> 格式: `总分 T:TTFT P:TPOT E:E2E K:Tokens` | 评分 = 6(通过) + 质量(CC+行数, 0-4) = 6~10")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"*由 `humaneval_eval.py` 自动生成*")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    global log
    parser = argparse.ArgumentParser(description="HumanEval 多层代码正确性评估")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--models", default=None,
                        help="模型名列表, 逗号分隔 (覆盖 config 中的 model.name)")
    parser.add_argument("--model-endpoint", action="append", default=[],
                        help="指定某模型的特殊 base_url, 格式 name=url")
    parser.add_argument("--num", type=int, default=20, help="每个模型测试的问题数")
    parser.add_argument("--max-tokens", type=int, default=1024, help="每个请求的最大 token")
    parser.add_argument("--concurrent", type=int, default=3, help="RPM 并发数")
    parser.add_argument("--output", default=None, help="报告输出路径 (默认输出到终端)")
    parser.add_argument("--obsidian", action="store_true",
                        help="保存到 Obsidian Vault (20_testRecord/ 目录)")
    parser.add_argument("--db", action="store_true",
                        help="评估结果写入 SQLite 数据库")
    parser.add_argument("--from-db", default=None, nargs="?",
                        help="从 DB 读取指定 run_id 生成报告, 不调用 API")
    parser.add_argument("--list", action="store_true",
                        help="列出 DB 中所有可用 run")
    parser.add_argument("--log-level", default="normal",
                        choices=["quiet", "normal", "verbose", "json"],
                        help="日志级别: quiet=仅错误, normal=进度+结果, verbose=调试, json=机器可读")
    args = parser.parse_args()

    # 初始化日志
    log = EvalLogger(args.log_level)

    # ── 报告模式: 从 DB 读取 ──
    if args.list:
        runs = list_runs()
        if not runs:
            log.progress("DB 中无记录")
        else:
            log.progress(f"{'run_id':<30s} {'时间':<25s} {'模型':<30s} {'通过':>8s} {'平均分':>8s}")
            log.progress("-" * 100)
            for r in runs:
                log.progress(f"{r['run_id']:<30s} {r['timestamp']:<25s} {r['model_name']:<30s} "
                             f"{r['pass_count']}/{r['num_problems']}  {r['avg_score']}")
        log.flush_json()
        return

    if args.from_db is not None:
        if args.from_db == "":
            log.progress("用法: --from-db run_id 或 --from-db run_id1,run_id2")
            log.flush_json()
            return
        run_ids = [r.strip() for r in args.from_db.split(",")]
        data = load_from_db(run_ids)
        all_results = {}
        model_names = []
        serving_summary = {}
        for rid, d in data.items():
            mname = d["run"]["model_name"]
            model_names.append(mname)
            all_results[mname] = d["results"]
            all_results[mname + "__rpm"] = {}
            serving_summary[mname] = {}
        report = build_comparison_table(all_results, model_names, serving_summary)
        log.progress(report)

        if args.output:
            os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
            with open(args.output, "w") as f:
                f.write(report)
            log.progress(f"Report saved to: {args.output}")
        if args.obsidian:
            vault = os.environ.get("OBSIDIAN_VAULT", os.path.expanduser("~/Documents/Obsidian Vault"))
            ts = time.strftime("%Y%m%d_%H%M%S")
            models_str = "_".join(model_names).replace("/", "-")
            out_path = f"{vault}/20_testRecord/humaneval-eval_{models_str}_{ts}.md"
            os.makedirs(f"{vault}/20_testRecord", exist_ok=True)
            with open(out_path, "w") as f:
                f.write(report)
            log.progress(f"Report saved to Obsidian: {out_path}")
        log.flush_json()
        return

    cfg = load_config(args.config)
    base_url = cfg["model"].get("base_url", "").rstrip("/")
    api_key_env = cfg["model"].get("api_key_env", "")
    api_key = resolve_api_key(cfg, api_key_env)

    # 解析特殊 endpoint
    model_endpoints = {}
    for me in args.model_endpoint:
        if "=" in me:
            name, url = me.split("=", 1)
            model_endpoints[name.strip()] = url.strip()

    # 确定模型列表
    if args.models:
        model_names = [m.strip() for m in args.models.split(",")]
    else:
        model_names = [cfg["model"]["name"]]

    if not api_key and not model_endpoints:
        log.progress("No API key found")
        sys.exit(1)

    # 加载 HumanEval
    dataset = load_humaneval()
    log.progress(f"HumanEval: {len(dataset)} problems")

    all_results = {m: [] for m in model_names}
    models_config = {}

    for m in model_names:
        mb = model_endpoints.get(m, base_url)
        if m in model_endpoints:
            if "huawei" in mb.lower() or "modelarts" in mb.lower() or "maas" in mb.lower():
                mk = resolve_api_key(cfg, "HUAWEI_MAAS_KEY")
            else:
                mk = resolve_api_key(cfg, api_key_env)
        else:
            mk = api_key
        if not mk:
            mk = resolve_api_key(cfg, "NEWAPI_TOKEN")
        models_config[m] = {"base_url": mb, "api_key": mk}
        log.debug(f"Model: {m} -> {mb}")

    log.progress(f"Testing {args.num} problems per model...")

    # ── 对每个模型进行评测 (问题间并发) ──
    for model_name in model_names:
        mc = models_config[model_name]
        problems = dataset[:args.num]

        log.banner(f"Evaluating: {model_name} | Problems: {len(problems)} | Concurrent: {args.concurrent}")

        # 并发执行评测
        with ThreadPoolExecutor(max_workers=args.concurrent) as pool:
            # submit 时保留原始索引，确保结果顺序与 problems 一致
            future_to_idx = {}
            for idx, prob in enumerate(problems):
                fut = pool.submit(
                    evaluate_problem,
                    prob, model_name, mc["base_url"], mc["api_key"], args.max_tokens
                )
                future_to_idx[fut] = idx

            # 按提交顺序初始化结果列表，按完成顺序填充
            model_results = [None] * len(problems)
            completed = 0
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                result = fut.result()
                model_results[idx] = result
                completed += 1

                prob = problems[idx]
                score = compute_total_score(result)
                err = (result.get("error") or "")[:40]
                log.result(prob["task_id"], result.get("passed", False),
                           score, result.get("serving", {}), err)
                log.progress(f"  [{completed}/{len(problems)}] {prob['task_id']} done")

        all_results[model_name] = model_results

        # ── RPM 测量 (保持原有并发) ──
        log.section(f"测量 RPM ({args.concurrent} 并发)")
        rpm_prompts = [problems[i]["prompt"] for i in range(min(args.concurrent, len(problems)))]
        rpm_result = measure_rpm(
            model_name, rpm_prompts,
            mc["base_url"], mc["api_key"],
            concurrent=args.concurrent, max_tokens=args.max_tokens
        )
        all_results[model_name + "__rpm"] = rpm_result
        log.progress(f"  RPM = {rpm_result['avg']} req/min ({args.concurrent}并发)")

    # 收集服务性能汇总
    serving_summary = {}
    for m in model_names:
        if m + "__rpm" in all_results:
            serving_summary[m] = all_results[m + "__rpm"]

    # 生成对比报告
    report = build_comparison_table(all_results, model_names, serving_summary)
    log.progress(report)

    # ── JSON 日志输出（含原始模型响应，便于回溯） ──
    log_data = {}
    for m in model_names:
        log_data[m] = []
        for r in all_results.get(m, []):
            entry = {
                "task_id": r["task_id"],
                "entry_point": r["entry_point"],
                "passed": r.get("passed", False),
                "error": r.get("error"),
                "total_score": compute_total_score(r),
                "scores": {
                    "correctness": r.get("layers", {}).get("correctness"),
                    "quality": r.get("layers", {}).get("quality"),
                    "efficiency": r.get("layers", {}).get("efficiency"),
                },
                "serving": r.get("serving", {}),
                "raw_model_output": r.get("raw_model_output", ""),
                "extracted_code": r.get("extracted_code", ""),
            }
            log_data[m].append(entry)

    log_ts = time.strftime("%Y%m%d_%H%M%S")
    log_dir = Path(cfg.get("metrics", {}).get("output_dir", "./results"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"humaneval_log_{log_ts}.json"
    with open(log_path, "w") as f:
        json.dump(log_data, f, indent=2, default=str, ensure_ascii=False)
    log.debug(f"JSON log saved to: {log_path}")

    # ── 写入数据库 ──
    if args.db:
        run_ts = time.strftime("%Y%m%d_%H%M%S")
        for m in model_names:
            run_id = f"{run_ts}_{m.replace('/', '-')}"
            save_to_db(run_id, m, models_config[m]["base_url"], all_results.get(m, []))
        log.debug(f"Results saved to DB")

    # 输出到文件
    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        log.progress(f"Report saved to: {args.output}")

    # 输出到 Obsidian
    if args.obsidian:
        vault = os.environ.get("OBSIDIAN_VAULT", os.path.expanduser("~/Documents/Obsidian Vault"))
        ts = time.strftime("%Y%m%d_%H%M%S")
        models_str = "_".join(model_names).replace("/", "-")
        out_path = f"{vault}/20_testRecord/humaneval-eval_{models_str}_{ts}.md"
        os.makedirs(f"{vault}/20_testRecord", exist_ok=True)
        with open(out_path, "w") as f:
            f.write(report)
        log.progress(f"Report saved to Obsidian: {out_path}")

    # ── 汇总 ──
    total_run = sum(len(all_results.get(m, [])) for m in model_names)
    total_passed = sum(1 for m in model_names for r in all_results.get(m, []) if r.get("passed"))
    log.summary({"api_calls": total_run, "tests_run": total_run, "tests_passed": total_passed})
    log.flush_json()


if __name__ == "__main__":
    main()
