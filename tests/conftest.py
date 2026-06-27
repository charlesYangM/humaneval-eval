"""共享 fixture — 临时数据库、HumanEval 样本数据"""
import os
import sys
import json
import sqlite3
import pytest

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """提供一个隔离的临时数据库，不污染生产 humaneval_eval.db"""
    db_path = str(tmp_path / "test_eval.db")
    monkeypatch.setattr("humaneval_eval.DB_PATH", db_path)
    # 重新初始化表
    from humaneval_eval import _init_db
    _init_db()
    yield db_path


@pytest.fixture
def humaneval_sample():
    """一个典型的 HumanEval 问题样本 (基于 HumanEval/0 has_close_elements)"""
    return {
        "task_id": "HumanEval/0",
        "entry_point": "has_close_elements",
        "prompt": "from typing import List\n\n\ndef has_close_elements(numbers: List[float], threshold: float) -> bool:\n    \"\"\"Check if in given list of numbers, are any two numbers closer to each other than\n    given threshold.\n    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)\n    False\n    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)\n    True\n    \"\"\"\n",
        "test": "def check(candidate):\n    assert candidate([1.0, 2.0, 3.0], 0.5) == False\n    assert candidate([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3) == True\n    assert candidate([1.0, 2.0, 3.0], 0.05) == False\n    assert candidate([1.1, 2.2, 0.0], 1.0) == True\n\ndef test_check():\n    check(has_close_elements)\n",
        "canonical_solution": "    for idx, elem in enumerate(numbers):\n        for idx2, elem2 in enumerate(numbers):\n            if idx != idx2:\n                distance = abs(elem - elem2)\n                if distance < threshold:\n                    return True\n\n    return False\n",
    }


@pytest.fixture
def passing_result():
    """一个完整的通过结果 (用于 compute_total_score / DB 测试)"""
    return {
        "task_id": "HumanEval/0",
        "entry_point": "has_close_elements",
        "model": "test-model",
        "passed": True,
        "error": None,
        "layers": {
            "correctness": {"passed": True, "error": None, "duration": 0.001, "assertions": 4, "passed_assertions": 4},
            "quality": {"cyclomatic_complexity": 4, "lines": 8, "cc_score": 2, "lines_score": 1, "total": 3},
            "efficiency": {"sol_time": 0.0001, "can_time": 0.0001, "ratio": 1.0, "score": 0.5},
        },
        "serving": {
            "ttft_s": 0.15,
            "tpot_s_per_token": 0.005,
            "e2e_s": 1.2,
            "est_tokens": 200,
            "input_tokens": 100,
        },
        "raw_model_output": "```python\ndef has_close_elements(numbers, threshold):\n    for idx, elem in enumerate(numbers):\n        for idx2, elem2 in enumerate(numbers):\n            if idx != idx2:\n                distance = abs(elem - elem2)\n                if distance < threshold:\n                    return True\n    return False\n```",
        "extracted_code": "def has_close_elements(numbers, threshold):\n    for idx, elem in enumerate(numbers):\n        for idx2, elem2 in enumerate(numbers):\n            if idx != idx2:\n                distance = abs(elem - elem2)\n                if distance < threshold:\n                    return True\n    return False",
    }


@pytest.fixture
def failing_result():
    """一个完整的失败结果"""
    return {
        "task_id": "HumanEval/1",
        "entry_point": "separate_paren_groups",
        "model": "test-model",
        "passed": False,
        "error": "AssertionError: ",
        "layers": {
            "correctness": {"passed": False, "error": "AssertionError: ", "duration": None, "assertions": 3, "passed_assertions": 0},
        },
        "serving": {
            "ttft_s": 0.2,
            "e2e_s": 0.8,
        },
        "raw_model_output": "def separate_paren_groups(paren_string: str) -> List[str]:\n    return []",
        "extracted_code": "def separate_paren_groups(paren_string: str) -> List[str]:\n    return []",
    }
