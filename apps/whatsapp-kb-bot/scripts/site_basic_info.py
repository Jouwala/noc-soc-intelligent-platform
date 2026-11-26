#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""站点基本信息（Basic Info）查询 —— 对齐 GIS 页面 ?page=gis 下钻抽屉的 Basic Info。

用法（独立跑，便于人工核对 / 测试）：
    python site_basic_info.py "Palm Bay"     # 按站名，命中多条则全部返回（并列显示）
    python site_basic_info.py "3067"         # 按站号
    python site_basic_info.py "Gbangasiakole"  # 查无此站 → candidates 给相近站名

生产调用：被 scripts/eflow-live-alarms.py 的子进程 import，结果作为 JSON 的
``site_info`` 字段随站点告警回答一并返回（见 src/sitealarms.js 的渲染）。
放在同一个子进程里是为了复用已经探测好的“装齐 sqlalchemy 的解释器”，零额外进程开销。

## 数据源与口径
主库 = my_app 的 PostgreSQL 生产库 ``app.master_site_db``（实时，与 GIS 页面同源）。
降级 = SQLite 副本 ``db/app.db``（13 个字段实测齐全，但有时延；``source`` 会标 sqlite）。
字段对齐 SiteDrilldownDrawer.tsx 的 Basic Info：站号/站名/区域/省县/网络制式/
TRM 类型（前端标签 Site Type）/VIP/站点等级（site_caregory，已归一化）/坐标/FME+电话。

## 为什么不用 /api/site-attribute
那个接口是「站点属性」（VIP/枢纽/子类型），只返回**一条**（_exact_match），
字段也不含 network_type / trm_type / site_caregory / 坐标 / FME 电话。
本需求要的是 GIS Basic Info 的**全字段**且**同名多条并列**，故独立查询。

## 退出码
    0  成功（含 found=false —— 查无此站是有效答案，不是错误）
    2  库不可用 / 参数不合法
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
try:
    import my_app_paths  # noqa: E402
    MY_APP_DIR = my_app_paths.find_my_app_dir()
except Exception:
    MY_APP_DIR = os.environ.get('MY_APP_DIR') or ''
if MY_APP_DIR and MY_APP_DIR not in sys.path:
    sys.path.insert(0, MY_APP_DIR)

# 逻辑字段 → 候选列名（PG 蛇形 / SQLite 标题式）。与 gis.py::_GIS_FILTER_COLS 对齐。
_FIELD_COLS = {
    'site_id': ['site_id', 'Site ID'],
    'site_name': ['site_name', 'Site Name'],
    'region': ['region', 'Region'],
    'province_county': ['province_county', 'Province/County', 'Province'],
    'network_type': ['network_type', 'Network Type'],
    'trm_type': ['trm_type', 'TRM Type'],
    'vip_site': ['vip_site', 'VIP Site', 'vipsite'],
    'site_caregory': ['site_caregory', 'Site Category'],
    'site_type': ['site_type', 'Site Type'],
    'fme_name': ['fme_name', 'FME Name'],
    'fme_contact_1': ['fme_contact_1', 'FME Contact 1'],
    'latitude': ['latitude', 'Latitude'],
    'longitude': ['longitude', 'Longitude'],
}
_TABLE = 'master_site_db'
_MAX_RECORDS = 8       # 同名最多并列几条（库里同名最多 2 条，留余量）
_MAX_CANDIDATES = 8


def _connect():
    from backend.database import get_raw_connection
    return get_raw_connection()


def _table_ref(conn):
    """PG 生产库表名带 app schema；SQLite 副本直接是表名。"""
    try:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='app'"
        ).fetchall()
        if _TABLE in {r[0] for r in rows}:
            return 'app.' + _TABLE
    except Exception:
        pass
    return '"' + _TABLE + '"'


def _resolve_cols(conn, ref):
    """探测实际列名 → {逻辑字段: 实际列名}。"""
    # ⚠️ PG 下 PRAGMA 不报错、返回**空集**（不是异常）—— 所以不能只在 except 里兜底，
    #    「PRAGMA 返回空」也必须转 information_schema，否则 PG 生产库列探测全空。
    have = set()
    try:
        have = {r[1] for r in conn.execute(f'PRAGMA table_info({ref})').fetchall()}
    except Exception:
        pass
    if not have:
        try:
            have = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='app' AND table_name='master_site_db'").fetchall()}
        except Exception:
            pass
    out = {}
    for logical, cands in _FIELD_COLS.items():
        for c in cands:
            if c in have:
                out[logical] = c
                break
    return out


def _norm_category(value):
    """site_caregory 归一化 → NORMAL / COOL / ERA（口径同 gis.py::_norm_site_category）。"""
    if value is None:
        return ''
    s = str(value).strip()
    if not s:
        return ''
    s = re.sub(r'[（(][^（()）]*[)）]', '()', s)
    s = s.replace('(', '').replace(')', '').replace('（', '').replace('）', '')
    key = s.upper().replace(' ', '').replace('_', '')
    if key in ('REGULAR', 'NORMAL'):
        return 'NORMAL'
    if key == 'COOL':
        return 'COOL'
    if key == 'ERA':
        return 'ERA'
    return key or s


def _clean(value):
    if value is None:
        return ''
    s = str(value).strip()
    return '' if s in ('--', '-', 'None', 'nan') else s


def _pick(row, cols, logical):
    col = cols.get(logical)
    if not col:
        return ''
    try:
        return row[col]
    except (KeyError, IndexError, TypeError):
        return ''


def _as_number(v):
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _record(row, cols):
    return {
        'site_id': _clean(_pick(row, cols, 'site_id')),
        'site_name': _clean(_pick(row, cols, 'site_name')),
        'region': _clean(_pick(row, cols, 'region')),
        'province_county': _clean(_pick(row, cols, 'province_county')),
        'network_type': _clean(_pick(row, cols, 'network_type')),
        'trm_type': _clean(_pick(row, cols, 'trm_type')),
        'vip_site': _clean(_pick(row, cols, 'vip_site')),
        'site_caregory': _norm_category(_pick(row, cols, 'site_caregory')),
        'site_type': _clean(_pick(row, cols, 'site_type')),
        'fme_name': _clean(_pick(row, cols, 'fme_name')),
        'fme_contact_1': _clean(_pick(row, cols, 'fme_contact_1')),
        'latitude': _as_number(_pick(row, cols, 'latitude')),
        'longitude': _as_number(_pick(row, cols, 'longitude')),
    }


def _like(name):
    esc = str(name).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return '%' + esc.lower() + '%'


def _candidates(conn, ref, name_col, query):
    """查无此站时给相近站名：按查询词字母前缀从长到短 LIKE 'prefix%'。"""
    letters = ''.join(ch for ch in str(query).lower() if ch.isalpha())
    out = []
    for n in (7, 6, 5, 4):
        if len(letters) < n:
            continue
        try:
            rows = conn.execute(
                f'SELECT {name_col} FROM {ref} '
                f'WHERE LOWER(CAST({name_col} AS TEXT)) LIKE ? LIMIT {_MAX_CANDIDATES}',
                (letters[:n] + '%',),
            ).fetchall()
        except Exception:
            continue
        seen = set()
        for r in rows:
            name = _clean(r[0])
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
        if out:
            break
    return out[:_MAX_CANDIDATES]


def fetch(query):
    """按站名或站号查站点 Basic Info。永不抛异常（失败返回 error + found=false）。"""
    q = str(query or '').strip()
    if not q:
        return {'query': q, 'found': False, 'records': [], 'candidates': [],
                'source': 'none', 'error': '站点名/站号不能为空'}

    conn = None
    try:
        conn = _connect()
        ref = _table_ref(conn)
        cols = _resolve_cols(conn, ref)
        name_col = cols.get('site_name')
        id_col = cols.get('site_id')
        if not name_col and not id_col:
            return {'query': q, 'found': False, 'records': [], 'candidates': [],
                    'source': 'none', 'error': 'master_site_db 缺少站名/站号列'}

        clauses, params, by_id = [], [], False
        # 站号优先：纯数字（含前后空格）先按站号精确查 —— 用户问「站点 3067」。
        if id_col and re.fullmatch(r'\d+', q):
            clauses.append(f'CAST({id_col} AS TEXT) = ?')
            params.append(q)
            by_id = True
        if name_col:
            clauses.append(f'LOWER(CAST({name_col} AS TEXT)) = ?')
            params.append(q.lower())
        sql = f'SELECT * FROM {ref} WHERE ' + ' OR '.join(clauses)
        rows = conn.execute(sql, params).fetchall()

        # 精确没命中 → 站名模糊（站号查询不做模糊，避免 3067 命中 30670 之类）
        if not rows and name_col and not by_id:
            rows = conn.execute(
                f'SELECT * FROM {ref} WHERE LOWER(CAST({name_col} AS TEXT)) LIKE ? '
                f'ORDER BY {name_col} LIMIT {_MAX_RECORDS}',
                (_like(q),),
            ).fetchall()
            if rows:
                target = q.lower()
                exact = [r for r in rows
                         if _clean(_pick(r, cols, 'site_name')).lower() == target]
                rows = exact or rows

        records = [_record(r, cols) for r in rows[:_MAX_RECORDS]]
        candidates = [] if records else (
            _candidates(conn, ref, name_col, q) if name_col else [])
        source = 'sqlite'
        try:
            from backend.database import DB_MODE
            source = 'live' if DB_MODE == 'pg' else 'sqlite'
        except Exception:
            pass
        return {
            'query': q, 'by_id': by_id, 'found': bool(records),
            'count': len(records), 'records': records,
            'candidates': candidates, 'source': source,
        }
    except Exception as e:  # noqa: BLE001 - 站点信息是附加信息，失败不能影响告警主答案
        return {'query': q, 'found': False, 'records': [], 'candidates': [],
                'source': 'none', 'error': f'{type(e).__name__}: {e}'}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description='站点基本信息查询（GIS Basic Info 口径）')
    ap.add_argument('site', help='站点名或站号')
    args = ap.parse_args()
    print(json.dumps(fetch(args.site), ensure_ascii=False, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
