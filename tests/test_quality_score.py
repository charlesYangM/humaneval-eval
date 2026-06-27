"""测试 quality_score + 辅助函数 (cyclomatic_complexity, effective_lines)"""
import pytest
from humaneval_eval import quality_score, cyclomatic_complexity, effective_lines


class TestCyclomaticComplexity:
    """McCabe 圈复杂度"""

    def test_simple_function(self):
        code = "def add(a, b):\n    return a + b"
        assert cyclomatic_complexity(code) == 1  # 基线

    def test_single_if(self):
        code = "def check(x):\n    if x > 0:\n        return True\n    return False"
        assert cyclomatic_complexity(code) == 2  # 基线 + 1 if

    def test_multiple_branches(self):
        code = "def classify(x):\n    if x > 0:\n        return 1\n    elif x < 0:\n        return -1\n    else:\n        return 0"
        cc = cyclomatic_complexity(code)
        assert cc == 3  # 基线 + if + elif

    def test_for_loop(self):
        code = "def sum_list(lst):\n    total = 0\n    for x in lst:\n        total += x\n    return total"
        assert cyclomatic_complexity(code) == 2  # 基线 + for

    def test_while_loop(self):
        code = "def countdown(n):\n    while n > 0:\n        n -= 1"
        assert cyclomatic_complexity(code) == 2  # 基线 + while

    def test_syntax_error(self):
        assert cyclomatic_complexity("def foo(:\n    pass") == 999

    def test_empty_code(self):
        assert cyclomatic_complexity("") == 1  # 基线

    def test_bool_op_and(self):
        code = "def check(a, b):\n    if a and b:\n        return True"
        cc = cyclomatic_complexity(code)
        assert cc >= 2  # 基线 + if (BoolOp adds extra via ast.BoolOp)


class TestEffectiveLines:
    """有效代码行数"""

    def test_basic(self):
        code = "def foo():\n    return 1\n\n# comment\n"
        assert effective_lines(code) == 2

    def test_only_comments(self):
        code = "# line 1\n# line 2\n"
        assert effective_lines(code) == 0

    def test_empty_string(self):
        assert effective_lines("") == 0

    def test_mixed(self):
        code = "import os\n\ndef bar():\n    # helper\n    return 42"
        assert effective_lines(code) == 3  # import, def, return


class TestQualityScore:
    """代码质量评分"""

    def test_simple_solution_max_cc(self):
        """简单解法: cc=1 → cc_score=3, 行数合理 → lines_score=1, total=4"""
        solution = "def add(a, b):\n    return a + b"
        canonical = "def add(a, b):\n    return a + b"
        result = quality_score(solution, canonical)
        assert result["cc_score"] == 3
        assert result["total"] <= 4

    def test_complex_solution_low_cc_score(self):
        """复杂解法: cc>10 → cc_score=0"""
        # 构造一个高复杂度的代码
        lines = ["def complex_fn(x):"]
        for i in range(12):
            lines.append(f"    if x == {i}:")
            lines.append(f"        return {i}")
        lines.append("    return -1")
        solution = "\n".join(lines)
        result = quality_score(solution, "")
        assert result["cc_score"] == 0

    def test_very_long_solution_negative_lines(self):
        """行数超过标准解 2 倍 → lines_score=-1"""
        canonical = "def foo():\n    return 1"
        # 25 行 vs 2 行 = 12.5x → lines_score=-1
        lines = ["def foo():"]
        for i in range(25):
            lines.append(f"    x = {i}  # padding")
        lines.append("    return 1")
        solution = "\n".join(lines)
        result = quality_score(solution, canonical)
        assert result["lines_score"] == -1

    def test_concise_solution(self):
        """精简解法: 行数 ≤ 1.2x 标准解 → lines_score=1"""
        solution = "def double(x):\n    return x * 2"
        canonical = "def double(x):\n    return x * 2"
        result = quality_score(solution, canonical)
        assert result["lines_score"] == 1

    def test_total_clamp_to_zero(self):
        """cc_score=0 + lines_score=-1 → total=0 (clamp)"""
        # 构造: 高 cc + 超长代码
        lines = ["def complex_fn(x):"]
        for i in range(12):
            lines.append(f"    if x == {i}:")
            lines.append(f"        return {i}")
        for i in range(20):
            lines.append(f"    y = {i}  # pad")
        lines.append("    return -1")
        solution = "\n".join(lines)
        canonical = "def complex_fn(x):\n    return -1"
        result = quality_score(solution, canonical)
        assert result["total"] >= 0  # 不低于 0

    def test_total_clamp_to_four(self):
        """total 不超过 4"""
        # cc_score=3 + lines_score=1 = 4, 这是上限
        solution = "def add(a, b):\n    return a + b"
        canonical = "def add(a, b):\n    return a + b"
        result = quality_score(solution, canonical)
        assert result["total"] <= 4

    def test_empty_canonical(self):
        """标准解为空时，行数比较退化为与自身比较"""
        solution = "def foo():\n    return 1"
        result = quality_score(solution, "")
        # canonical 为空时 can_lines = lines, ratio = 1.0 ≤ 1.2 → lines_score=1
        assert result["lines_score"] == 1
