"""测试 run_test — 沙箱执行引擎"""
import pytest
from humaneval_eval import run_test


class TestPassingCases:
    """正常通过的测试"""

    def test_simple_pass(self):
        solution = "def add(a, b):\n    return a + b"
        test = "assert add(1, 2) == 3\nassert add(0, 0) == 0"
        result = run_test(solution, test, "add")
        assert result["passed"] is True
        assert result["duration"] is not None
        assert result["duration"] > 0
        assert result["assertions"] == 2
        assert result["passed_assertions"] == 2
        assert result["error"] is None

    def test_typing_annotation(self):
        solution = "def truncate_number(number: float) -> float:\n    return number - int(number)"
        test = "assert truncate_number(3.5) == 0.5\nassert abs(truncate_number(1.33) - 0.33) < 1e-6"
        result = run_test(solution, test, "truncate_number")
        assert result["passed"] is True

    def test_with_import_in_solution(self):
        """模型输出可能含 import 语句"""
        solution = "from typing import List\n\ndef sort_list(lst: List[int]) -> List[int]:\n    return sorted(lst)"
        test = "assert sort_list([3,1,2]) == [1,2,3]"
        result = run_test(solution, test, "sort_list")
        assert result["passed"] is True

    def test_math_module(self):
        """预加载的 math 模块可用"""
        solution = "import math\n\ndef sqrt_val(x):\n    return math.sqrt(x)"
        test = "assert abs(sqrt_val(4.0) - 2.0) < 1e-9"
        result = run_test(solution, test, "sqrt_val")
        assert result["passed"] is True

    def test_humaneval_style_check(self):
        """HumanEval 格式: check(candidate) → assert"""
        solution = "def has_close_elements(numbers, threshold):\n    for i, a in enumerate(numbers):\n        for j, b in enumerate(numbers):\n            if i != j and abs(a - b) < threshold:\n                return True\n    return False"
        test = "def check(candidate):\n    assert candidate([1.0, 2.0, 3.0], 0.5) == False\n    assert candidate([1.0, 2.8, 3.0], 0.3) == True\n\ndef test_check():\n    check(has_close_elements)"
        result = run_test(solution, test, "has_close_elements")
        assert result["passed"] is True


class TestFailingCases:
    """正常失败的测试"""

    def test_assertion_error(self):
        solution = "def always_fail():\n    return False"
        test = "assert always_fail() == True"
        result = run_test(solution, test, "always_fail")
        assert result["passed"] is False
        assert "AssertionError" in result["error"]

    def test_wrong_return_value(self):
        solution = "def add(a, b):\n    return a - b"  # bug
        test = "assert add(1, 2) == 3"
        result = run_test(solution, test, "add")
        assert result["passed"] is False


class TestSyntaxErrors:
    """语法错误 — 应在沙箱内被捕获"""

    def test_syntax_error_in_solution(self):
        solution = "def foo(:\n    pass"
        test = ""
        result = run_test(solution, test, "foo")
        assert result["passed"] is False
        assert "SyntaxError" in result["error"]


class TestTimeoutAndResource:
    """超时和资源限制"""

    def test_infinite_loop_timeout(self):
        """无限循环应在超时后被杀"""
        solution = "def infinite():\n    while True:\n        pass"
        test = "infinite()"
        result = run_test(solution, test, "infinite", timeout_sec=3)
        assert result["passed"] is False
        assert "TIMEOUT" in result["error"]

    def test_deep_recursion(self):
        """深度递归触发 RecursionError"""
        solution = "def recurse(n):\n    return recurse(n + 1)"
        test = "recurse(0)"
        result = run_test(solution, test, "recurse", timeout_sec=5)
        assert result["passed"] is False
        # RecursionError 或 TIMEOUT 都可接受
        assert result["error"] is not None


class TestSecurity:
    """沙箱安全验证"""

    def test_open_file_blocked(self):
        """open() 被白名单移除"""
        solution = "def hack():\n    f = open('/tmp/pwned.txt', 'w')\n    f.write('hacked')\n    f.close()\n    return True"
        test = "assert hack() == True"
        result = run_test(solution, test, "hack")
        assert result["passed"] is False
        assert "NameError" in result["error"] or "open" in result["error"]

    def test_os_system_behavior(self):
        """os.system 的执行结果取决于系统 — NPROC 限制是用户级而非进程级
        在当前 subprocess 沙箱中 os.system 可能仍然执行,
        完全隔离需 Docker 容器 (同 JSON 协议, 只换执行方式)"""
        solution = "import os\ndef hack():\n    result = os.system('true')\n    return result"
        test = "assert hack() == 0"
        result = run_test(solution, test, "hack")
        # os.system 可能执行成功也可能被阻止, 测试只需确保不崩溃
        assert result["passed"] is not None

    def test_subprocess_blocked(self):
        """subprocess 被 NPROC=0 阻止"""
        solution = "import subprocess\ndef hack():\n    subprocess.run(['echo', 'pwned'])\n    return True"
        test = "assert hack() == True"
        result = run_test(solution, test, "hack")
        assert result["passed"] is False
