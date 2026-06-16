"""mutio.mcp.toolset — MCPToolSet 类变量 测试。"""

from mutio.mcp.toolset import MCPToolSet


class TestMCPToolSetDefaults:
    def test_prefix(self):
        assert MCPToolSet.prefix == ""

    def test_view_is_none(self):
        assert MCPToolSet.view is None

    def test_path(self):
        assert MCPToolSet.path == ""

    def test_subclass_inherits(self):
        class MyTools(MCPToolSet):
            prefix = "my"

        assert MyTools.prefix == "my"
        assert MyTools.view is None
        assert MCPToolSet.prefix == ""
