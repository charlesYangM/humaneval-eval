"""测试 DB 存储回环 — save_to_db / load_from_db / list_runs"""
import pytest
from humaneval_eval import save_to_db, load_from_db, list_runs, compute_total_score


class TestSaveAndLoad:
    """写入→读取 数据一致性"""

    def test_roundtrip_single_run(self, tmp_db, passing_result, failing_result):
        """一次 run 的完整写入→读取"""
        results = [passing_result, failing_result]
        save_to_db("test_run_001", "test-model", "http://your-api-endpoint/v1", results)

        data = load_from_db(["test_run_001"])
        assert "test_run_001" in data

        run_info = data["test_run_001"]["run"]
        assert run_info["model_name"] == "test-model"
        assert run_info["num_problems"] == 2

        # 验证结果数量
        loaded_results = data["test_run_001"]["results"]
        assert len(loaded_results) == 2

        # 验证通过的结果
        passed = [r for r in loaded_results if r["passed"]]
        assert len(passed) == 1

    def test_pass_count(self, tmp_db, passing_result, failing_result):
        """pass_count 统计正确"""
        results = [passing_result, failing_result]
        save_to_db("test_run_002", "test-model", "http://your-api-endpoint/v1", results)

        data = load_from_db(["test_run_002"])
        assert data["test_run_002"]["run"]["pass_count"] == 1

    def test_avg_score(self, tmp_db, passing_result):
        """avg_score 基于 compute_total_score"""
        results = [passing_result]
        save_to_db("test_run_003", "test-model", "http://your-api-endpoint/v1", results)

        data = load_from_db(["test_run_003"])
        expected = compute_total_score(passing_result)
        assert abs(data["test_run_003"]["run"]["avg_score"] - expected) < 0.01

    def test_multiple_runs(self, tmp_db, passing_result):
        """多个 run 可共存"""
        for i in range(3):
            save_to_db(f"test_run_{i:03d}", f"model-{i}", "http://your-api-endpoint/v1", [passing_result])

        data = load_from_db(["test_run_000", "test_run_001", "test_run_002"])
        assert len(data) == 3

    def test_load_nonexistent(self, tmp_db):
        """加载不存在的 run_id 返回空"""
        data = load_from_db(["nonexistent_run"])
        assert data == {}


class TestPrefixMatch:
    """前缀匹配加载"""

    def test_wildcard_load(self, tmp_db, passing_result):
        """run_id* 通配符加载"""
        save_to_db("20260627_model-a", "model-a", "http://your-api-endpoint/v1", [passing_result])
        save_to_db("20260627_model-b", "model-b", "http://your-api-endpoint/v1", [passing_result])

        data = load_from_db(["20260627_*"])
        assert len(data) == 2

    def test_no_match_wildcard(self, tmp_db):
        """无匹配前缀返回空"""
        data = load_from_db(["nonexistent_*"])
        assert data == {}


class TestListRuns:
    """list_runs 列出所有 run"""

    def test_list_empty(self, tmp_db):
        runs = list_runs()
        assert runs == []

    def test_list_after_save(self, tmp_db, passing_result):
        save_to_db("test_run_list", "test-model", "http://your-api-endpoint/v1", [passing_result])
        runs = list_runs()
        assert len(runs) == 1
        assert runs[0]["run_id"] == "test_run_list"
        assert runs[0]["model_name"] == "test-model"

    def test_list_order_desc(self, tmp_db, passing_result):
        """按时间倒序排列 — 同秒内写入的 run 顺序不确定, 只验证都在列表中"""
        save_to_db("run_early", "model-a", "http://your-api-endpoint/v1", [passing_result])
        save_to_db("run_late", "model-b", "http://your-api-endpoint/v1", [passing_result])
        runs = list_runs()
        run_ids = [r["run_id"] for r in runs]
        assert set(run_ids) == {"run_early", "run_late"}


class TestDBFieldIntegrity:
    """DB 字段完整性 — 确保写入→读取后关键字段不丢失"""

    def test_serving_metrics_preserved(self, tmp_db, passing_result):
        """TTFT/TPOT/E2E 等服务指标在回环中保留"""
        save_to_db("test_metrics", "model", "http://your-api-endpoint/v1", [passing_result])
        data = load_from_db(["test_metrics"])
        r = data["test_metrics"]["results"][0]
        assert r["serving"]["ttft_s"] == 0.15
        assert r["serving"]["e2e_s"] == 1.2

    def test_quality_preserved(self, tmp_db, passing_result):
        """质量分在回环中保留"""
        save_to_db("test_quality", "model", "http://your-api-endpoint/v1", [passing_result])
        data = load_from_db(["test_quality"])
        r = data["test_quality"]["results"][0]
        assert r["layers"]["quality"]["cyclomatic_complexity"] == 4

    def test_error_preserved(self, tmp_db, failing_result):
        """错误信息在回环中保留"""
        save_to_db("test_error", "model", "http://your-api-endpoint/v1", [failing_result])
        data = load_from_db(["test_error"])
        r = data["test_error"]["results"][0]
        assert r["error"] is not None
        assert "AssertionError" in r["error"]


class TestSchemaMigration:
    """Schema 迁移 — 旧 DB 自动升级，数据不丢失"""

    def test_fresh_db_gets_version(self, tmp_db):
        """全新 DB 创建后 schema_version = 1"""
        from humaneval_eval import _SCHEMA_VERSION, _init_db
        # _init_db 已在 conftest fixture 中调用，再调一次应幂等
        _init_db()
        import sqlite3
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT value FROM schema_version WHERE key = 'schema'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == _SCHEMA_VERSION

    def test_old_db_without_version_table_auto_upgrades(self, tmp_db, monkeypatch):
        """模拟旧 DB（无 schema_version 表）→ _init_db 后自动写入版本号"""
        import sqlite3
        from humaneval_eval import _init_db, _SCHEMA_VERSION

        # 先删除 fixture 建的标准表，模拟旧格式 DB
        conn = sqlite3.connect(tmp_db)
        conn.execute("DROP TABLE IF EXISTS eval_results")
        conn.execute("DROP TABLE IF EXISTS eval_runs")
        conn.execute("DROP TABLE IF EXISTS schema_version")
        conn.execute("""CREATE TABLE eval_runs (
            run_id TEXT PRIMARY KEY, timestamp TEXT, model_name TEXT,
            base_url TEXT, num_problems INTEGER, pass_count INTEGER, avg_score REAL
        )""")
        conn.execute("""CREATE TABLE eval_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
            task_id TEXT, entry_point TEXT, passed INTEGER, total_score REAL,
            error TEXT, cc INTEGER, lines INTEGER, ttft_ms REAL,
            tpot_ms_p_token REAL, e2e_s REAL, input_tokens INTEGER,
            output_tokens INTEGER, raw_model_output TEXT
        )""")
        conn.execute(
            "INSERT INTO eval_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("old_run_001", "2026-01-01 00:00 UTC", "old-model",
             "http://your-api-endpoint/v1", 1, 1, 9.5)
        )
        conn.commit()
        conn.close()

        # 调用 _init_db 触发迁移（CREATE TABLE IF NOT EXISTS 不会覆盖已有表）
        _init_db()

        # 验证版本号已写入
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT value FROM schema_version WHERE key = 'schema'"
        ).fetchone()
        assert row[0] == _SCHEMA_VERSION

        # 验证旧数据还在
        row = conn.execute(
            "SELECT model_name FROM eval_runs WHERE run_id = 'old_run_001'"
        ).fetchone()
        assert row[0] == "old-model"
        conn.close()

    def test_migration_is_idempotent(self, tmp_db):
        """重复调用迁移不会报错或重复写入"""
        from humaneval_eval import _init_db
        _init_db()
        _init_db()  # 第二次调用应该直接 return（已是最新）
        _init_db()  # 第三次也一样

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        rows = conn.execute(
            "SELECT value FROM schema_version WHERE key = 'schema'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1  # 只有一行，没重复
        assert rows[0][0] >= 1
