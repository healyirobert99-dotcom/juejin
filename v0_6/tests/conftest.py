"""
v0.6 测试隔离 fixture

自动为每个测试函数提供独立的临时交易数据库，
使用 monkeypatch 安全替换 DB_PATH，异常时自动恢复。
"""
import pytest


@pytest.fixture(autouse=True)
def isolated_trade_db(tmp_path, monkeypatch):
    """每个测试使用独立的临时 SQLite 文件，替换全局 DB_PATH

    - yield 后由 monkeypatch 自动恢复原路径
    - 测试异常退出时也不会污染全局状态
    - fixture 本身不连接任何数据库
    """
    import v0_6.core.config as cfg
    import v0_6.core.live_trade_store as lts

    db_path = tmp_path / "test_trade.sqlite3"
    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    monkeypatch.setattr(lts, "DB_PATH", db_path)

    yield db_path
