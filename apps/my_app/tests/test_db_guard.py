# -*- coding: utf-8 -*-
"""db_guard 熔断器回归测试（2026-11-23 后端故障后新增）。

锁定的行为：
- 连续 N 次**连接级**失败后熔断，熔断期内建连请求立即失败、不再调用底层 connect；
- 到期后只放行一个探测请求（half-open）：成功即恢复，失败再熔断一个周期；
- SQL/参数类错误与数据库可达性无关，不计数、不触发熔断；
- 调用方传入的 timeout 只收紧不放宽（历史调用普遍传 30，是 sqlite 的 busy 语义）；
- DatabaseUnavailable 是 psycopg2.OperationalError 的子类，既有 except 分支行为不变。
"""
import pytest

from backend import db_guard as g


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _breaker(threshold=3, open_seconds=10):
    clk = _Clock()
    return g._Breaker(threshold, open_seconds, clock=clk), clk


def _conn_error():
    psycopg2 = pytest.importorskip("psycopg2")
    return psycopg2.OperationalError(
        'connection to server at "192.168.100.20", port 5432 failed: timeout expired')


def test_opens_after_threshold_then_fails_fast():
    b, _ = _breaker(threshold=3)
    for _ in range(2):
        b.before_connect()
        b.record_failure(RuntimeError("timeout expired"))
    assert b.state == "closed"
    b.before_connect()
    b.record_failure(RuntimeError("timeout expired"))
    assert b.state == "open"
    with pytest.raises(g.DatabaseUnavailable) as ei:
        b.before_connect()
    assert ei.value.retry_after >= 1
    assert b.snapshot()["rejected_total"] == 1


def test_half_open_allows_single_probe_and_closes_on_success():
    b, clk = _breaker(threshold=1, open_seconds=10)
    b.record_failure(RuntimeError("x"))
    assert b.state == "open"
    clk.t += 11
    assert b.state == "half_open"
    b.before_connect()                      # 第一个请求成为探测
    with pytest.raises(g.DatabaseUnavailable):
        b.before_connect()                  # 探测进行中，其余请求仍快速失败
    b.record_success()
    assert b.state == "closed"
    b.before_connect()                      # 恢复后正常放行


def test_half_open_probe_failure_reopens_for_full_period():
    b, clk = _breaker(threshold=3, open_seconds=10)
    for _ in range(3):
        b.record_failure(RuntimeError("x"))
    clk.t += 11
    b.before_connect()
    b.record_failure(RuntimeError("still down"))
    assert b.state == "open"
    clk.t += 5
    assert b.state == "open"                # 从探测失败的时刻重新计时
    clk.t += 6
    assert b.state == "half_open"


def test_threshold_zero_disables_breaker():
    b, _ = _breaker(threshold=0)
    for _ in range(10):
        b.record_failure(RuntimeError("x"))
        b.before_connect()                  # 不抛
    assert b.state == "closed"


def test_effective_timeout_only_tightens(monkeypatch):
    monkeypatch.setattr(g, "PG_CONNECT_TIMEOUT", 5)
    assert g.effective_connect_timeout(30) == 5
    assert g.effective_connect_timeout(2) == 2
    assert g.effective_connect_timeout(None) == 5
    assert g.effective_connect_timeout("bad") == 5


def test_guarded_connect_counts_connection_errors_and_stops_calling(monkeypatch):
    err = _conn_error()
    b, _ = _breaker(threshold=2)
    monkeypatch.setattr(g, "breaker", b)
    calls = []

    def bad_connect():
        calls.append(1)
        raise err

    for _ in range(2):
        with pytest.raises(g.DatabaseUnavailable):
            g.guarded_connect(bad_connect)
    assert b.state == "open"
    with pytest.raises(g.DatabaseUnavailable):
        g.guarded_connect(bad_connect)
    assert len(calls) == 2                  # 熔断后不再真正建连


def test_guarded_connect_ignores_non_connection_errors(monkeypatch):
    b, _ = _breaker(threshold=1)
    monkeypatch.setattr(g, "breaker", b)

    def bad_args():
        raise ValueError("invalid dsn option")

    with pytest.raises(ValueError):
        g.guarded_connect(bad_args)
    assert b.state == "closed"
    assert b.snapshot()["consecutive_failures"] == 0


def test_guarded_connect_success_resets(monkeypatch):
    b, _ = _breaker(threshold=3)
    monkeypatch.setattr(g, "breaker", b)
    b.record_failure(RuntimeError("x"))
    assert g.guarded_connect(lambda: "conn") == "conn"
    assert b.snapshot()["consecutive_failures"] == 0


def test_database_unavailable_is_psycopg2_operational_error():
    psycopg2 = pytest.importorskip("psycopg2")
    assert issubclass(g.DatabaseUnavailable, psycopg2.OperationalError)


def test_is_connection_error_classification():
    psycopg2 = pytest.importorskip("psycopg2")
    assert g.is_connection_error(_conn_error())
    assert g.is_connection_error(psycopg2.OperationalError("server closed the connection unexpectedly"))
    assert g.is_connection_error(psycopg2.InterfaceError("connection already closed"))
    assert not g.is_connection_error(ValueError("timeout expired"))       # 不是 DBAPI 错误
    assert not g.is_connection_error(psycopg2.ProgrammingError("syntax error at or near"))

    class _Wrapped(Exception):  # 模拟 SQLAlchemy 的包装：原始异常在 .orig
        def __init__(self, orig):
            super().__init__(str(orig))
            self.orig = orig

    assert g.is_connection_error(_Wrapped(_conn_error()))
