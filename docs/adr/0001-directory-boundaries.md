# ADR 0001：目录边界

- 状态：已接受
- 日期：2026-11-13
- 范围：第一轮重构，`refactor/round-1`

## 背景

02（`D:\02_olbr_portal`）原来是便携副本的旧目录：根上散落着长文档、中文目录、
`Task\`、`scripts\`，子系统之间没有明确的边界说明。几次事故都源于目录关系被
误改：bot 与 my_app 拆开导致问答控制找不到 bot；`eflow_Q&A` 改名或移位导致
知识库默认路径失效；`_fe_dist` 放错一级导致前端白屏。

## 决策

仓库顶层只留三类目录：`apps/`（子系统）、`docs/`（文档）、`ops/`（运维脚本），
另有不入库的 `runtime/`（捆绑运行时）。

硬性相对关系（事实依据见方案第 3.1–3.3 节，相对关系总表见
`docs/architecture/module-map.md`）：

1. `my_app` 与 `whatsapp-kb-bot` 必须同级，bot 的叶子名不改。
2. `eflow_Q&A` 必须在 `my_app` 的父目录（`apps/eflow_Q&A`），叶子名不改，
   不放进 `docs`。
3. `_fe_dist` 必须在 `my_app` 的上一级（`apps/_fe_dist`），构建产物不入库。
4. 根上只留 `AGENTS.md` 和 `README.md`；长文档进 `docs/workspace/`。

## 后果

- 正面：相对路径只依赖「向上几级」，`Task\` → `apps\` 的搬家不用改找 bot、
  找知识库、找构建产物的逻辑。
- 负面：任何想改这三处相对关系的人，必须同时改代码里的找法、启动脚本和
  `ops/quality/check_all.ps1` 第 9 步的断言，不能只挪目录。
- 校验：`check_all.ps1` 第 9 步断言这三处相对关系；CI 不直接断言目录位置，
  由本地检查覆盖。

## 相关

- 方案第 3.1、3.2、3.3 节；`docs/architecture/module-map.md`。
