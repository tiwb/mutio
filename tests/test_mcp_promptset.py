"""mutio.mcp.promptset — MCPPromptSet 类变量 测试。"""

from mutio.mcp.promptset import MCPPromptSet


class TestMCPPromptSetDefaults:
    def test_prefix(self):
        assert MCPPromptSet.prefix == ""

    def test_view_is_none(self):
        assert MCPPromptSet.view is None

    def test_path(self):
        assert MCPPromptSet.path == ""

    def test_subclass_inherits(self):
        class MyPrompts(MCPPromptSet):
            prefix = "my_"

        assert MyPrompts.prefix == "my_"
        assert MyPrompts.view is None
        assert MCPPromptSet.prefix == ""
