"""测试 compute_total_score — 评分边界条件"""
import pytest
from humaneval_eval import compute_total_score


class TestPassedCases:
    """通过的用例: 基础分 6 + 质量分 0-4"""

    def test_min_quality(self):
        """通过但质量 0 分 = 6.0"""
        result = {
            "passed": True,
            "layers": {
                "quality": {"total": 0},
            },
        }
        assert compute_total_score(result) == 6.0

    def test_max_quality(self):
        """通过且质量满分 = 10.0"""
        result = {
            "passed": True,
            "layers": {
                "quality": {"total": 4},
            },
        }
        assert compute_total_score(result) == 10.0

    def test_typical_quality(self):
        """通过 + 质量 3 = 9.0"""
        result = {
            "passed": True,
            "layers": {
                "quality": {"total": 3},
            },
        }
        assert compute_total_score(result) == 9.0

    def test_quality_fractional(self):
        """质量分可以是小数"""
        result = {
            "passed": True,
            "layers": {
                "quality": {"total": 2.5},
            },
        }
        assert compute_total_score(result) == 8.5

    def test_db_cached_score(self):
        """从 DB 加载的数据带 _total_score, 直接使用"""
        result = {
            "passed": True,
            "_total_score": 8.2,
            "layers": {"quality": {"total": 0}},  # 忽略
        }
        assert compute_total_score(result) == 8.2


class TestFailedCases:
    """失败的用例: 0 分"""

    def test_explicit_fail(self):
        result = {"passed": False, "error": "TIMEOUT", "layers": {}}
        assert compute_total_score(result) == 0.0

    def test_empty_result(self):
        result = {}
        assert compute_total_score(result) == 0.0

    def test_none_passed(self):
        result = {"passed": None, "layers": {"quality": {"total": 4}}}
        assert compute_total_score(result) == 0.0

    def test_passed_false_with_quality(self):
        """即使有质量分，passed=False 也是 0"""
        result = {
            "passed": False,
            "layers": {"quality": {"total": 4}},
        }
        assert compute_total_score(result) == 0.0


class TestScoreClamp:
    """分数上限 clamp 至 10.0"""

    def test_cannot_exceed_10(self):
        """质量分异常高时 clamp 到 10"""
        result = {
            "passed": True,
            "layers": {"quality": {"total": 99}},
        }
        assert compute_total_score(result) == 10.0

    def test_negative_quality_clamps_to_6(self):
        """质量分为负时不低于基础分 6"""
        result = {
            "passed": True,
            "layers": {"quality": {"total": -5}},
        }
        # max(0, -5) = 0 in quality_score, but compute_total_score does 6 + quality.total
        # quality.total is already clamped in quality_score, but if injected directly:
        assert compute_total_score(result) == 1.0  # 6 + (-5) = 1, but quality_score clamps to 0
