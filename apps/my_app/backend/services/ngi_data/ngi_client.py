# -*- coding: utf-8 -*-
"""NGI 规划平台（10.100.29.17:28001）取数客户端。

## 接口面（来自 `backend/services/ngi_data/url.txt` 的浏览器抓包）

| 用途 | 请求 |
| --- | --- |
| 登录 / 探活 | `GET /api/ngi-common-service-ms/v1/taskmanager/gettasklogbytaskid?taskid=<TASK_ID>` |
| 工参导出 | `GET /api/ngi-common-service-ms/v1/projectParamFiles?exportType=1,0` → `ProjectData.zip` |
| 最新覆盖任务 | 同上 `gettasklogbytaskid`，取 `data.data[0].taskLogId` 与 `taskLogName` |
| 评估指标清单 | `POST /api/ngi-common-service-ms/v1/jobprocess/jsonresult/CovEvaluationCopKPI` |
| 图层与图例 | `POST /api/ngi-common-service-ms/v1/commonGis/getLayerFiles` |
| 覆盖网格导出（异步） | `POST /ngi-gis3d-mapserver/v1/MapServerService/exportMap` → 令牌 |
| 导出进度 | `POST .../exportMapChecking` → `errorcode=1118` 处理中 / `0` 完成 |
| 下载 | `POST .../downloadExportMapFile` → `CSV.zip` |

## 鉴权：不照抄抓包里的 Cookie

`url.txt` 里的 `Z-AUTH-CODE-28001` / `x-dexmesh-user-28001` / `forgerydefense-28001`
是会过期的浏览器会话。本模块复用项目既有的
`auth_manager.get_auth(..., probe=...)` + `credential_store` 的 `ngi` 凭据组，
与 RAN UME 系列任务同一套「跨进程锁 + 共享会话缓存」逻辑：

* 探活打的是**本任务自己的业务端点**（`gettasklogbytaskid`），不是登录首页 ——
  首页对未登录也返回 200，探首页会得到假绿（见 `auth_manager.py` 顶部约束 1）；
* 会话失效时 `get_auth` 就地作废缓存并真实重登一次；
* 不调用登出：会话是共享的，登出会踢掉同名账号的其它会话。

## 覆盖导出的两个硬约束（实测 2026-11-24）

1. **exportMap 的 `params` 必须带 `legend`**（`getLayerFiles` 返回的
   `name` / `showtitle` / `range` 原样回传）。只给
   `taskID + renderType + layerName + colDes` 时服务端照样返回令牌，
   但作业**永远停在 `processing`**（实测 4 个作业 30 分钟仍未完成）；
   带上 legend 后 4 次轮询（约 15 秒）即 finished。少一个 legend 不报错，
   只静默挂起 —— 这是本模块最容易踩的坑。
2. **下载走 `downloadExportMapFile`**（表单 `{mapType, params: 令牌}`），
   不是 `exportMapChecking`。文件名固定 `mapType + ".zip"`，
   见前端 `ngi-novisualization/assets/webgis/build/gis3d.js` 的 `saveFile()`。
   前端轮询退避为 5s → 60s → 5min，本模块按同样的量级设计超时。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Callable, TypeVar

import requests

logger = logging.getLogger("ngi_client")

try:  # 上游是内网自签证书，对应抓包里的 --insecure
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception as exc:  # noqa: BLE001
    logger.debug("关闭 urllib3 警告失败: %s", exc)

# ---------------------------------------------------------------- 配置
HOST = os.environ.get("NGI_HOST", "10.100.29.17")
PORT = int(os.environ.get("NGI_PORT", "28001"))
BASE = f"https://{HOST}:{PORT}"

# 覆盖评估任务的「任务号」。页面上的任务清单接口按它取**最新一次**运行记录，
# 因此不能把 taskLogId（如 11014）写死 —— 它每次评估都会变。
TASK_ID = os.environ.get("NGI_TASK_ID", "1647")

CONNECT_TIMEOUT = float(os.environ.get("NGI_CONNECT_TIMEOUT", "15"))
READ_TIMEOUT = float(os.environ.get("NGI_READ_TIMEOUT", "180"))
# 覆盖网格导出是服务端异步作业：实测带 legend 时 ~15s 完成，
# 但上游队列繁忙时可能按分钟计，默认 30 分钟封顶（与前端 5 分钟档轮询同量级）。
EXPORT_TIMEOUT = int(os.environ.get("NGI_EXPORT_TIMEOUT", "1800"))
POLL_FAST_SEC = 5
POLL_SLOW_SEC = 30
POLL_STEP = 10

API = "/api/ngi-common-service-ms/v1"
GIS = "/ngi-gis3d-mapserver/v1/MapServerService"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")

T = TypeVar("T")


class AuthExpired(RuntimeError):
    """业务端点返回 401/403：会话在探活之后、取数之前失效。"""


class UpstreamError(RuntimeError):
    """上游返回了非预期结构（不是 401/403，重登也无意义）。"""


# ---------------------------------------------------------------- 底层
def _session() -> requests.Session:
    """内网直连：绕开系统代理（trust_env=False）+ 忽略自签证书（verify=False）。"""
    s = requests.Session()
    s.trust_env = False
    s.verify = False
    return s


_SESSION = _session()


def _headers(cookie: str, forgery_token: str) -> dict:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "User-Agent": USER_AGENT,
        "Referer": f"{BASE}/ngi-novisualization/",
        "X-Requested-With": "XMLHttpRequest",
        "language-option": "zh-CN",
        "ngiUserName": "admin",
        "timeZoneInfo": "Default,Atlantic/Reykjavik,",
        "Cookie": cookie,
        "forgerydefense": forgery_token,
    }


def _check(resp: requests.Response, what: str) -> requests.Response:
    if resp.status_code in (401, 403):
        raise AuthExpired(f"{what} 返回 HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise UpstreamError(f"{what} 返回 HTTP {resp.status_code}: {resp.text[:200]}")
    return resp


def _probe(cookie: str, forgery_token: str) -> bool:
    """供 `auth_manager.get_auth(probe=...)` 探活。

    必须探业务端点（探登录首页会拿到 200 假绿）；用 `stream=True` 只取状态行
    就关闭，避免探活白白下载一次任务日志。
    """
    try:
        r = _SESSION.get(
            f"{BASE}{API}/taskmanager/gettasklogbytaskid",
            params={"taskid": TASK_ID},
            headers=_headers(cookie, forgery_token),
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            stream=True,
        )
        code = r.status_code
        r.close()
        alive = code not in (401, 403)
        if not alive:
            logger.info("[NGI] 共享会话已失效（探活 gettasklogbytaskid -> %s）", code)
        return alive
    except Exception as e:  # noqa: BLE001
        logger.warning("[NGI] 会话探活异常（%s: %s），按不可用处理", type(e).__name__, e)
        return False


def _runtime_credentials() -> tuple[str, str]:
    """凭据：环境变量 > credential_store(`ngi`) > 组内默认值。"""
    from backend.services.credential.credential_store import get_credential

    return get_credential("ngi", env_user="NGI_USER", env_secret="NGI_PASSWORD")


def acquire_auth(force_refresh: bool = False) -> tuple[str, str, str]:
    """取一份「已确认可用」的共享会话，返回 (cookie, forgery_token, 来源)。"""
    from backend.services.auth_manager import get_auth

    user, pwd = _runtime_credentials()
    if not user or not pwd:
        raise RuntimeError(
            "ngi 凭据为空：请在「系统设置 → 凭据」维护 NGI 规划平台（10.100.29.17:28001）的账号口令"
        )
    r = get_auth(HOST, PORT, user, pwd, probe=_probe, force_refresh=force_refresh)
    source = "缓存" if r["from_cache"] else (
        "重登（探活失败）" if r.get("from_relogin") else "重登")
    logger.info("[NGI] 鉴权成功（来源: %s）", source)
    return r["cookie"], r["forgery_token"], source


def with_auth(fetch: Callable[[str, str], T]) -> tuple[T, str]:
    """执行一次需要会话的取数；遇到 401/403 强制重登后重试一次。"""
    cookie, token, source = acquire_auth()
    try:
        return fetch(cookie, token), source
    except AuthExpired:
        logger.warning("[NGI] 取数遇到 401/403，强制重登后重试一次")
        cookie, token, source = acquire_auth(force_refresh=True)
        return fetch(cookie, token), source


def _json(resp: requests.Response, what: str) -> Any:
    try:
        return resp.json()
    except ValueError as e:
        raise UpstreamError(
            f"{what} 返回的不是 JSON（HTTP {resp.status_code}，{len(resp.content)} 字节，"
            f"前 120 字符：{resp.text[:120]!r}）"
        ) from e


def _post_json(cookie: str, token: str, path: str, body: dict, what: str,
               timeout: float | None = None) -> requests.Response:
    """按前端 `postByJSONData` 的写法发 JSON（`Content-Type: application/json`）。"""
    return _check(
        _SESSION.post(
            f"{BASE}{path}",
            data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={**_headers(cookie, token), "Content-Type": "application/json"},
            timeout=(CONNECT_TIMEOUT, timeout or READ_TIMEOUT),
        ),
        what,
    )


# ---------------------------------------------------------------- 工参
def fetch_param_zip(cookie: str, forgery_token: str) -> bytes:
    """导出工参 → `ProjectData.zip`（约 1.9 MB，含 LTE / NR 两个 xlsx）。

    `exportType` 是**制式列表去重**：`1,0` = NR + LTE 都导；`0,0` 只导 LTE；
    `1,1` 只导 NR（实测 2026-11-24）。默认 `1,0` 覆盖两种制式。
    """
    resp = _SESSION.get(
        f"{BASE}{API}/projectParamFiles",
        params={"exportType": os.environ.get("NGI_PARAM_EXPORT_TYPE", "1,0")},
        headers=_headers(cookie, forgery_token),
        timeout=(CONNECT_TIMEOUT, 600),
    )
    _check(resp, "projectParamFiles")
    data = resp.content
    if not data.startswith(b"PK"):
        raise UpstreamError(
            f"工参导出不是 ZIP（{len(data)} 字节，Content-Type="
            f"{resp.headers.get('Content-Type')!r}，前 80 字节：{data[:80]!r}）"
        )
    return data


# ---------------------------------------------------------------- 覆盖评估
def latest_task(cookie: str, forgery_token: str) -> dict:
    """取该评估任务**最新一次**运行记录（taskLogId 每次评估都会变）。"""
    resp = _SESSION.get(
        f"{BASE}{API}/taskmanager/gettasklogbytaskid",
        params={"taskid": TASK_ID},
        headers=_headers(cookie, forgery_token),
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
    )
    _check(resp, "gettasklogbytaskid")
    data = _json(resp, "gettasklogbytaskid")
    rows = ((data or {}).get("data") or {}).get("data") or []
    if not rows:
        raise UpstreamError(f"taskid={TASK_ID} 没有任何任务日志，无法确定最新评估批次")
    row = rows[0]
    if not row.get("taskLogId"):
        raise UpstreamError(f"最新任务日志缺 taskLogId：{row!r}")
    return row


def kpi_templates(cookie: str, forgery_token: str, task_log: dict) -> list[dict]:
    """该次评估可导出的指标清单（实测只有 `LTE_RSRP@@RSRP(dBm)` 一项）。

    返回 `[{"rule": "LTE_RSRP", "label": "RSRP(dBm)"}]`；上游新增 5G 指标时
    本函数会自动带出来，导出循环无需改代码。
    """
    body = {
        "taskLogId": str(task_log.get("taskLogId")),
        "taskLogName": task_log.get("taskLogName"),
        "tasklogid": str(task_log.get("taskLogId")),
        "ratType": None,
    }
    resp = _post_json(cookie, forgery_token,
                      f"{API}/jobprocess/jsonresult/CovEvaluationCopKPI", body,
                      "CovEvaluationCopKPI")
    data = _json(resp, "CovEvaluationCopKPI")
    table = ((data or {}).get("data") or {}).get("table") or []
    out: list[dict] = []
    for row in table[1:]:  # 第一行是表头 [value, lable, isselected]
        if not row:
            continue
        value = str(row[0] or "").strip()
        label = str(row[1] or "").strip() if len(row) > 1 else ""
        if not value:
            continue
        rule = value.split("@@")[0].strip()
        if rule:
            out.append({"rule": rule, "label": label or rule, "value": value})
    if not out:
        raise UpstreamError(f"CovEvaluationCopKPI 没有返回任何可导出指标：{data!r}"[:300])
    return out


def layer_and_legend(cookie: str, forgery_token: str, task_log_id: str,
                     rule: str) -> tuple[str, str, int, dict]:
    """取网格图层的 `taskID`（含 hash）、图层名、网格米数与 legend。

    返回 `(task_id, layer_name, grid_size, legend)`。`task_id` 形如
    `11014-393711f02eae4afb8dfc9dbd11bb47e0`，是 exportMap 的必需参数；
    其中 hash 只能由本接口给出（不在任务日志里）。
    """
    body = {"tasklogid": str(task_log_id), "rulename": rule,
            "rendertype": "grid", "maptype": 0}
    resp = _post_json(cookie, forgery_token, f"{API}/commonGis/getLayerFiles", body,
                      f"getLayerFiles({rule})")
    data = _json(resp, "getLayerFiles")
    layers = str((data or {}).get("mapserverlayers") or "")
    m = re.search(r"layer-(\d+)-([0-9a-f]{32})-grid-(\d+)-(\d+)", layers)
    if not m:
        raise UpstreamError(
            f"getLayerFiles({rule}) 未返回网格图层（mapserverlayers={layers!r}）"
        )
    task_id = f"{m.group(1)}-{m.group(2)}"
    grid_size = int(m.group(4))
    legend = (data or {}).get("Legend") or {}
    if not legend.get("range"):
        raise UpstreamError(f"getLayerFiles({rule}) 的 Legend.range 为空，导出会永远 processing")
    return task_id, "LTE_Grid_Layer", grid_size, legend


def export_grid_zip(cookie: str, forgery_token: str, task_id: str, layer_name: str,
                    grid_size: int, legend: dict, rule: str) -> bytes:
    """触发网格 CSV 导出 → 轮询到完成 → 下载 ZIP。

    参数形状（含 legend）是与页面 `gis3d.js` 对齐后的实测结果，
    **删掉 legend 会让作业永远停在 processing**，见模块顶部约束 1。
    """
    kpi_name = rule.split("_")[-1] or rule
    inner = [{
        "taskID": task_id,
        "renderType": "Grid",
        "layerName": layer_name,
        "colDes": [f"gridsize={grid_size}", "map=0", "gridpoint=0", f"kpiname={kpi_name}"],
        "legend": {"name": legend.get("name"), "showtitle": legend.get("showtitle"),
                   "range": legend.get("range")},
    }]
    body = {"mapType": "CSV", "params": json.dumps(inner, separators=(",", ":"))}
    resp = _post_json(cookie, forgery_token, f"{GIS}/exportMap", body,
                      "exportMap", timeout=300)
    data = _json(resp, "exportMap")
    token = str((data or {}).get("object") or "")
    if not token:
        raise UpstreamError(f"exportMap 未返回导出令牌：{data!r}"[:300])
    logger.info("[NGI] exportMap 受理（rule=%s token=%s）", rule, token)

    started = time.monotonic()
    polls = 0
    while True:
        polls += 1
        resp = _post_json(cookie, forgery_token, f"{GIS}/exportMapChecking",
                          {"mapType": "CSV", "params": token},
                          "exportMapChecking", timeout=180)
        chk = _json(resp, "exportMapChecking")
        code = str((chk or {}).get("errorcode"))
        if code == "0":
            logger.info("[NGI] 导出完成（rule=%s 轮询 %d 次，%.1fs）",
                        rule, polls, time.monotonic() - started)
            break
        if code != "1118":
            raise UpstreamError(f"exportMapChecking 返回异常状态：{chk!r}"[:300])
        if time.monotonic() - started > EXPORT_TIMEOUT:
            raise UpstreamError(
                f"覆盖导出 {EXPORT_TIMEOUT}s 未完成（rule={rule} token={token}）；"
                "若近期改动过 exportMap 参数，先确认 legend 是否原样带上"
            )
        time.sleep(POLL_FAST_SEC if polls <= POLL_STEP else POLL_SLOW_SEC)

    resp = _post_json(cookie, forgery_token, f"{GIS}/downloadExportMapFile",
                      {"mapType": "CSV", "params": token},
                      "downloadExportMapFile", timeout=600)
    data = resp.content
    if not data.startswith(b"PK"):
        raise UpstreamError(
            f"覆盖导出下载不是 ZIP（{len(data)} 字节，Content-Type="
            f"{resp.headers.get('Content-Type')!r}，前 80 字节：{data[:80]!r}）"
        )
    return data
