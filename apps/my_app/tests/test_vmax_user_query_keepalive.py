# -*- coding: utf-8 -*-
"""VMAX 用户查询页的视图保活 + 条件恢复守卫（纯离线，只看源码文本）。

为什么要有这个文件：
    视图最初用 `{view === 'xdr' && <Panel/>}` 条件渲染，切走时面板被 React 卸载，
    查询结果一并销毁。修法是**四个视图默认全部挂载**、切走只 `display:none`
    （并行取数也要求它们同时在跑，不再用 mounted 懒挂载开关）。
    谁把条件渲染写回来，这里立刻失败。

    第二部分守的是「URL 恢复」：号码/视角落在 URL（`src/urlState.ts`），
    只允许 replaceState。

运行：
    cd D:/01_RPA/Task/my_app
    python tests/test_vmax_user_query_keepalive.py
"""
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
PAGE = SRC / "pages" / "VmaxUserQuery.tsx"
MERGE = SRC / "components" / "VmaxXdrPanel.tsx"
TABLE = SRC / "components" / "VmaxXdrRecordsPanel.tsx"
URLSTATE = SRC / "urlState.ts"
# 2026-10-30 统一时间窗后，窗口的唯一真相源搬到了 vmaxWindow.ts
# （三个页签共用一份 RangePicker，URL 参数 vqStart/vqEnd 也由它读写）。
# 父页与两个子面板都不再各自读自己的 mxStart/rtStart。
WINDOW = SRC / "pages" / "vmaxWindow.ts"

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s%s" % (name, ("  -> " + detail) if detail else ""))
        FAILS.append(name)


def main():
    src = PAGE.read_text(encoding="utf-8")

    # 1) 四视图默认全部挂载（无 mounted 懒挂载）；切走用 display 隐藏
    check("明确声明四视图默认全部挂载",
          "四个视图**默认全部挂载**" in src or "四个视图默认全部挂载" in src,
          "缺保活注释/约定，可能又变回按需挂载")
    check("diagnosis 视图 display 切换",
          re.search(r"display:\s*view === 'diagnosis' \? 'flex' : 'none'", src) is not None)
    check("flow 视图 display 切换",
          re.search(r"display:\s*view === 'flow' \? 'flex' : 'none'", src) is not None)
    check("connect 视图 display 切换",
          re.search(r"display:\s*view === 'connect' \? 'block' : 'none'", src) is not None)
    check("radio 视图 display 切换",
          re.search(r"display:\s*view === 'radio' \? 'flex' : 'none'", src) is not None)

    # 2) 反例：不再有直接按 view 短路渲染面板的写法
    for key, comp in (("connect", "VmaxXdrPanel"), ("radio", "VmaxXdrRecordsPanel"),
                      ("xdr", "VmaxXdrPanel"), ("records", "VmaxXdrRecordsPanel")):
        bad = re.search(
            r"\{view === '%s' &&\s*\(?\s*(//[^\n]*\n\s*)*<%s" % (key, comp), src)
        check("%s 未被 view=%s 条件渲染直接卸载" % (comp, key), bad is None,
              "出现 `{view === '%s' && < %s ...>`，切页签会丢数据" % (key, comp))

    # 3) 分类流量独立；Segmented 切视图（四视图已常挂载，用 setView 即可）
    check("分类流量页签已独立（顶层 flow 视图）",
          "'flow'" in src and "VmaxFlowCategoryTabs" in src,
          "VmaxUserQuery 里没有 flow 视图，分类页签可能又被塞回概览")
    check("概览不再嵌流量卡（VmaxCcaCards 不收 flow）",
          "flow={result.flow}" not in src,
          "概览还在拼 flow={result.flow}，分类流量没真正独立")
    check("Segmented 切视图",
          "onChange={(v) => setView(" in src or "onChange={setView}" in src,
          "页签切换必须更新 view，否则 display 保活失效")
    # ---- URL 恢复 ----
    check("urlState.ts 存在", URLSTATE.exists())
    if URLSTATE.exists():
        u = URLSTATE.read_text(encoding="utf-8")
        check("urlState 只用 replaceState（不污染后退栈）",
              "replaceState" in u and "pushState" not in u)
        check("urlState 写入保留 history.state",
              "window.history.state" in u,
              "覆盖 state 会打乱 App 的 ?page= 后退恢复")

    check("父页从 URL 读号码", "readParam('msisdn')" in src)
    check("父页从 URL 读视角", "readParam('vmaxView')" in src)
    # 2026-10-31 起时间窗滚动：首屏永远是「当时」的最近 3 小时，
    # URL 里不再记窗口（旧链接残留的 vqStart/vqEnd 忽略并清掉）。
    win = WINDOW.read_text(encoding="utf-8")
    check("首屏窗口滚动（不再重放 URL 里旧窗口）",
          "readParam('vqStart')" not in win and "readParam('vqEnd')" not in win,
          "vmaxWindow.ts 还在读 vqStart/vqEnd，旧链接会落回固定时间")
    check("首屏窗口走 defaultRange（最近 3 小时）",
          "return defaultRange()" in win)
    check("父页引用统一时间窗模块",
          "from './vmaxWindow'" in src or 'from "./vmaxWindow"' in src)
    check("父页查询时写号码（不是每次按键写）",
          re.search(r"writeParams\(\{\s*msisdn: q", src) is not None)
    check("父页首屏自动重放（URL 带号码才跑一次）", "bootRef" in src)

    merge = MERGE.read_text(encoding="utf-8")
    check("合并表面板读 mxSvc", "readParam('mxSvc')" in merge)
    # 窗口不再由面板自己读 URL，而是从页面级 context 取（三个页签同一份）
    check("合并表面板从 context 取窗口", "useVmaxWindow()" in merge)
    check("合并表面板查询后写回条件", "mxSvc: data.service_type" in merge)

    table = TABLE.read_text(encoding="utf-8")
    check("分表面板读 rtSvc/rtTable",
          "readParam('rtSvc')" in table and "readParam('rtTable')" in table)
    check("分表面板不再自己读窗口 URL（统一由 vmaxWindow.ts 管）",
          "readParam('rtStart')" not in table and "readParam('rtEnd')" not in table)
    check("分表面板从 context 取窗口", "useVmaxWindow()" in table)
    check("分表面板查询后写回条件", "rtTable: data.table" in table)
    check("分表面板换类型时保留仍在清单里的表",
          re.search(r"setTable\(\(cur\) =>", table) is not None,
          "直接 setTable(preferred) 会把 URL 恢复出来的表名冲掉")

    print("")
    if FAILS:
        print("FAILED: %d 项" % len(FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
