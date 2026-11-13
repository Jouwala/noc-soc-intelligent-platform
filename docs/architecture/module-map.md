# 模块地图

> 依据：《NOC-SOC-Intelligent-Platform最终重构执行方案》第 3.1–3.3 节（已核实的现状）。
> 本文件只写相对关系，不写绝对盘符；工作区根在 Windows 02 上是 `D:\02_olbr_portal`。

## 目录总览

```text
<仓库根>/
├── apps/
│   ├── my_app/            # Data Insight MVP（FastAPI + React）
│   ├── portal/            # 统一门户（React + Vite + Node 鉴权服务）
│   ├── whatsapp-kb-bot/   # WhatsApp 知识库问答 bot（Node）
│   ├── eflow_Q&A/         # eFlow 知识库（Markdown + Python）
│   └── _fe_dist/          # my_app 前端构建产物（不入库，运行时服务目录）
├── docs/
├── ops/
└── runtime/               # 捆绑运行时（不入库，见 runtime/README.md）
```

## 硬性相对关系

### 1. `my_app` 与 `whatsapp-kb-bot` 必须同级

两者都在 `apps/` 下，bot 的叶子名不改。以下代码按「`my_app` 的父目录 /
`whatsapp-kb-bot`」找 bot：

| 代码 | 实际找法 |
| --- | --- |
| `portal_qa_ctl.py` | `my_app` 的父目录 / `whatsapp-kb-bot` |
| `tt_alarm_render.py` | 同上，再进 `scripts\tt-analyze.py` |
| `wa_notify_service.py` | 文件向上 3 级，再接 `whatsapp-kb-bot` |
| 门户 `myAppPaths.js` | `src` 向上 3 级是仓库根；bot 的父目录 / `my_app` |

从 `Task\` 一起上移到 `apps\` 后，层数不变，这几处不用改逻辑。拆开即坏。

### 2. `eflow_Q&A` 必须在 `my_app` 的父目录，叶子名不改

即 `apps/eflow_Q&A`，不改名为 `eflow-qa`，不放进 `docs`。

- `backend/routers/eflow_qa.py` 的默认目录是：本文件向上 3 级 / `eflow_Q&A`。
- `main.py` 启动时会预热其中的 `tools\inspect_tt.py`。
- `my_app` 进 `apps` 之后，向上 3 级就是 `apps`，所以默认路径不用改。
- `start_all.ps1` 里的 `EFLOW_QA_DIR` 指向这个新路径，给经过启动脚本的进程用。

### 3. `_fe_dist` 必须在 `my_app` 的上一级

即 `apps/_fe_dist`。

- `apps/my_app/frontend/vite.config.ts` 的 `outDir` 是 `../../_fe_dist`。
- 后端 `apps/my_app/backend/main.py` 是「向上 3 级 / `_fe_dist`」
  （`frontend_dist = Path(__file__).parent.parent.parent / "_fe_dist"`）。
- 两处对齐，构建产物不入库（根 `.gitignore` 已排除 `apps/_fe_dist/`）。
  02 里如果还没有这个目录，不要为了搬家去创建空目录；构建后核对它落在
  `apps/_fe_dist`，防止写进 01 或 RPA。
- `nginx.conf` 用 alias 从磁盘直接读它，不经任何构建步骤；忽略跟踪 ≠ 可以删除。

## 启动禁令

- 02 **禁止**运行 `apps\my_app\start_hidden.ps1` 和同目录的 `start.bat`
 （那是生产启动脚本，误跑会打断正在看数据的人）。
- 隔离启动只走 `ops\deploy\start_all.ps1`（方案第 8.3 节）；启动前确认调度器
  关闭、WhatsApp bot 关闭、`.env` 是 02 的隔离配置，不是从 01 拷来的那份。
- 不要把 `apps/my_app/start_hidden.ps1` 之类的生产启动脚本搬到 `ops/deploy`。

## 校验

`ops/quality/check_all.ps1` 第 9 步断言：bot 引擎脚本
（`apps/whatsapp-kb-bot/scripts/tt-analyze.py`）、
`apps/eflow_Q&A/tools/inspect_tt.py`、`apps/_fe_dist` 三者的相对关系。
