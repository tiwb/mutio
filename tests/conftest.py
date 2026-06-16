"""mutio 测试配置 — 注册自定义 pytest marker。"""

import pytest


def pytest_configure(config):
    """注册 mutobj 测试方法论要求的自定义 marker。"""
    config.addinivalue_line(
        "markers",
        "l2(contract): L2 契约测试，验证内部状态机 / 失效时机 / 回退路径",
    )
