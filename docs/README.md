# docs — 文档目录

| 目录 | 内容 |
| --- | --- |
| `docs/workspace/` | 8 份体系长文档（架构、开发、TODO、项目概览、用户指南、DESIGN、组件 API、部署说明） |
| `docs/architecture/` | 架构现状：模块地图、数据源登记表 |
| `docs/adr/` | 架构决策记录（ADR） |
| `docs/operations/` | 运维记录（如回滚演练记录，见方案第 9 节） |
| `docs/archive/` | 历史档案（`PROJECT_DOCS.md`、`PROJECT_DOCS_ARCHIVE.md`；只存档，现状不写回） |

## 约定

- 现状以各子系统的 `docs/manual/` 与 `README.md` 为准；不要把现状写回
  `docs/archive/PROJECT_DOCS.md`（151KB 历史档案，其自身 README 已声明现状以
  `docs/manual/` 为准）。不要新增第二个真相源。
- 中文优先；文档里只出现配置项名称、环境变量键名、分组 key，**不得**出现口令、
  Token、授权码、账号、密码哈希。
- 改了页面、任务或文档里的数字后，必须跑对应的文档一致性守卫：
  门户 `cd apps/portal && npm run check:docs`，my_app
  `cd apps/my_app && python tests/test_doc_consistency.py`。
  两份守卫的数字句式（如门户 README 里「N 个 my_app 真实页面直链」）不要改写法，
  改了句式会导致守卫自己失效。
