"""测试 extract_function_code — 7 策略代码提取管道"""
import pytest
from humaneval_eval import extract_function_code


class TestStrategy1PythonBlock:
    """策略 1: ```python ... ``` 代码块"""

    def test_simple_python_block(self):
        answer = 'Here is the code:\n```python\ndef foo(x):\n    return x + 1\n```\nDone.'
        assert extract_function_code(answer, "foo") == "def foo(x):\n    return x + 1"

    def test_python_block_with_typing(self):
        answer = '```python\nfrom typing import List\n\ndef sort_list(lst: List[int]) -> List[int]:\n    return sorted(lst)\n```'
        result = extract_function_code(answer, "sort_list")
        assert result is not None
        assert "def sort_list" in result
        assert "from typing import List" in result

    def test_Python_capitalized(self):
        """```Python 也应被匹配"""
        answer = '```Python\ndef foo():\n    pass\n```'
        assert extract_function_code(answer, "foo") is not None


class TestStrategy2GenericBlock:
    """策略 2: ``` (无语言标记) 代码块"""

    def test_generic_code_block(self):
        answer = 'Result:\n```\ndef bar(n):\n    return n * 2\n```'
        assert extract_function_code(answer, "bar") is not None


class TestStrategy3CodeTag:
    """策略 3: <code> ... </code>"""

    def test_code_html_tag(self):
        answer = '<code>\ndef baz(s):\n    return s.upper()\n</code>'
        result = extract_function_code(answer, "baz")
        assert result is not None
        assert "def baz" in result


class TestStrategy4RegexDef:
    """策略 4: 正则查找 def + 前置 import"""

    def test_def_with_imports(self):
        answer = 'from typing import List\nimport math\n\ndef compute(x: float) -> float:\n    return math.sqrt(x)'
        result = extract_function_code(answer, "compute")
        assert result is not None
        assert "def compute" in result
        assert "import math" in result

    def test_def_without_imports(self):
        answer = 'def simple():\n    return 42'
        result = extract_function_code(answer, "simple")
        assert result is not None


class TestStrategy5BareDef:
    """策略 5: 回复直接以 def 开头"""

    def test_starts_with_def(self):
        answer = 'def add(a, b):\n    return a + b'
        result = extract_function_code(answer, "add")
        assert result is not None
        assert "def add" in result


class TestStrategy6IndentBody:
    """策略 6: 缩进函数体补全 def 签名"""

    def test_indented_body(self):
        answer = '    return x + 1'
        result = extract_function_code(answer, "guess")
        assert result is not None
        assert "def guess" in result
        assert "return x + 1" in result


class TestStrategy7LooseMatch:
    """策略 7: 松散匹配"""

    def test_def_in_middle_of_text(self):
        answer = 'Some explanation here.\ndef find_max(lst):\n    return max(lst)\nMore text after.'
        result = extract_function_code(answer, "find_max")
        assert result is not None
        assert "def find_max" in result

    def test_def_with_import_before(self):
        answer = 'We need to import:\nfrom typing import Optional\n\ndef maybe_val(x) -> Optional[int]:\n    if x > 0:\n        return x\n    return None'
        result = extract_function_code(answer, "maybe_val")
        assert result is not None


class TestEdgeCases:
    """边界情况"""

    def test_empty_input(self):
        assert extract_function_code("", "foo") is None
        assert extract_function_code(None, "foo") is None
        assert extract_function_code("   ", "foo") is None

    def test_wrong_entry_point(self):
        """代码存在但 entry_point 不匹配"""
        answer = '```python\ndef bar():\n    pass\n```'
        assert extract_function_code(answer, "foo") is None

    def test_natural_language_only(self):
        """纯自然语言回复，无代码"""
        answer = 'I think the solution involves sorting the array first, then iterating through it.'
        assert extract_function_code(answer, "solve") is None

    def test_multiple_blocks_first_wins(self):
        """多个代码块时，第一个有效块优先"""
        answer = '```python\ndef foo():\n    return 1\n```\nOr:\n```python\ndef foo():\n    return 2\n```'
        result = extract_function_code(answer, "foo")
        assert "return 1" in result

    def test_syntax_error_in_block_skipped(self):
        """有语法错误的代码块被跳过，尝试下一个"""
        answer = '```python\ndef foo(:\n    bad syntax\n```\n```python\ndef foo():\n    return 1\n```'
        result = extract_function_code(answer, "foo")
        assert result is not None
        assert "return 1" in result
