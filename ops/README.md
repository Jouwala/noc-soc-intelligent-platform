# ops — 运维脚本目录

| 目录 | 内容 |
| --- | --- |
| `ops/deploy/` | 02 副本的启停与运维脚本（入口 `start_all.ps1` / `stop_all.ps1`），详见其中 `README.md` |
| `ops/quality/` | 离线质量检查 `check_all.ps1`（完整 9 步，见下） |
| `ops/shortcuts/` | 桌面快捷方式 bat（状态、干跑、打开首页/日志等） |

## 质量检查

```powershell
cd D:\02_olbr_portal
powershell -NoProfile -File ops\quality\check_all.ps1
```

完整顺序（9 步）以脚本内注释与《NOC-SOC-Intelligent-Platform最终重构执行方案》
第 7 节批 E 为准：目录与 `ISOLATED_COPY.flag` → `.env` 端口 → 被跟踪文件
无凭据/数据库 → compileall → ruff → 门户 lint/test → 门户文档守卫 →
my_app 文档一致性守卫 → 路径断言。第 6 到 8 步在依赖或基线还没准备好时可以
单独跳过，但脚本必须打印跳过原因，不能假装通过；跳过的步骤汇总在脚本末尾。

同一份检查也在 CI 里跑（`.github/workflows/quality.yml`，`windows-latest`），
CI 不访问 UME、eFlow、生产库、邮箱、WhatsApp、内网地址，不跑还没标记好的全量
pytest。live 标记加完、离线集合有基线之后，再把 `pytest -m "not live"` 加进
CI，那是后续提交，不塞进目录搬家。

## 已停用的同步链

`ops/deploy/sync-engine.mjs`、`sync-policy.mjs`、`sync_from_main.ps1` 和
`bundle_data.ps1` 属于已停用的从 RPA（主仓库 `D:\01_RPA`）来的同步链，只保留
文件备查，**禁止对这个新目录跑从 RPA 来的同步**。根 `sync-now.bat` 已随批 C
删除。本仓库不再从 `D:\01_RPA` 同步。
