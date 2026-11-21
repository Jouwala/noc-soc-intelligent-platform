# -*- coding: utf-8 -*-
"""Weathermap 手工布局存储的单测（文件读写 / 归一化 / 上限）。"""
import pytest

from backend.services.ipran_topo import topo_layout_store as store


@pytest.fixture()
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PATH", str(tmp_path / "ipran_topo_layout.json"))
    return store


def test_roundtrip_and_reset(tmp_store):
    nodes, updated = tmp_store.save_layer("ipran", {"a": [1.234, 2.345], "b": [0, 0]})
    assert nodes == {"a": [1.2, 2.3], "b": [0.0, 0.0]}
    assert updated

    got, at = tmp_store.read_layer("ipran")
    assert got == nodes
    assert at == updated

    # 层之间互不影响
    assert tmp_store.read_layer("metro") == ({}, None)

    tmp_store.reset_layer("ipran")
    assert tmp_store.read_layer("ipran") == ({}, None)


def test_normalize_rejects_bad_input(tmp_store):
    with pytest.raises(ValueError):
        tmp_store.normalize({"a": ["x", 1]})
    with pytest.raises(ValueError):
        tmp_store.normalize({"a": [1]})
    with pytest.raises(ValueError):
        tmp_store.normalize({"a": [1, 99999]})
    with pytest.raises(ValueError):
        tmp_store.normalize({"": [1, 2]})
    with pytest.raises(ValueError):
        tmp_store.normalize([])


def test_normalize_limits(tmp_store, monkeypatch):
    monkeypatch.setattr(tmp_store, "MAX_NODES", 2)
    with pytest.raises(ValueError):
        tmp_store.normalize({"a": [1, 1], "b": [2, 2], "c": [3, 3]})


def test_broken_file_falls_back_to_empty(tmp_store):
    with open(tmp_store.PATH, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert tmp_store.read_layer("ipran") == ({}, None)
