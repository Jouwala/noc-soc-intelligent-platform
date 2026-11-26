#!/usr/bin/env bash
# bundle_data.sh —— 把生产 PostgreSQL 快照灌进离线副本的便携实例（Linux 版）
# =============================================================================
# 与 ops/deploy/bundle_data.ps1 同口径：两个阶段（可分开跑）：
#   dump     从主仓库 Task/my_app/backend/.env 的 DATABASE_URL 读生产连接信息，pg_dump -Fc 落盘
#   restore  副本 runtime/pgdata 首次 initdb -> pg_ctl start -> createdb -> pg_restore
#   all      两者都做（默认）
#
# 本文件**不含任何真实口令/Token**：口令在运行时从 SourceEnv 读进内存，
# 只通过进程环境变量 PGPASSWORD 传给 pg_dump/psql，用完立即清除；连接串不进日志、
# 不落盘、不写进副本的 .env（副本用 trust 认证的回环连接）。
#
# 与 ps1 的三处 Linux 差异（有意为之）：
#   1. --main-repo 在 Linux 上没有 D:\01_RPA 这样的默认主仓库路径；跑 dump/all 时
#      必须给 --main-repo 或 --source-env，否则报错退出。
#   2. PG 工具默认用副本自带的 PGDG 布局 runtime/postgresql16/lib/postgresql/16/bin
#     （见《安装与运行说明.md》第四节），不是 ps1 的 runtime/postgresql16/bin。
#   3. restore 拉起便携 PG 时 pg_ctl -o 另加 -k <副本>/TMP/pgsock，与 start.sh 一致
#     （#194：PGDG 默认 socket 目录 /var/run/postgresql 非 postgres 用户写不进）。
#
# 用法（在**能连到生产库的机器**上跑）：
#   bash ops/deploy/bundle_data.sh --dry-run
#   bash ops/deploy/bundle_data.sh --stage all
#   bash ops/deploy/bundle_data.sh --stage restore --dump-file TMP/myapp_snapshot_<时间戳>.dump
# =============================================================================
set -euo pipefail

step() { printf '[*] %s\n' "$*"; }
ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die() { printf '[ERR] %s\n' "$*" >&2; exit 1; }

# 去掉首尾空白（不含换行）
trim() { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

usage() {
  cat <<'EOF'
用法：bash ops/deploy/bundle_data.sh [选项]

  --main-repo <目录>    主仓库目录（默认空；跑 dump/all 时必须给，或改给 --source-env）
  --copy-root <目录>    副本根（默认按脚本位置推导：上两级目录）
  --source-env <文件>   生产 .env 路径（默认 <主仓库>/Task/my_app/backend/.env）
  --pg-bin <目录>       pg_dump/psql 所在目录（默认 <副本>/runtime/postgresql16/lib/postgresql/16/bin）
  --stage <all|dump|restore>  阶段（默认 all）
  --dump-file <文件>    快照路径（默认 <副本>/TMP/myapp_snapshot_<时间戳>.dump）
  --dry-run             干跑：只打印计划，不写盘、不起服务
  -h, --help            显示本帮助
EOF
}

MAIN_REPO=''
COPY_ROOT=''
SOURCE_ENV=''
PG_BIN=''
STAGE='all'
DUMP_FILE=''
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --main-repo) MAIN_REPO="$2"; shift 2 ;;
    --main-repo=*) MAIN_REPO="${1#--main-repo=}"; shift ;;
    --copy-root) COPY_ROOT="$2"; shift 2 ;;
    --copy-root=*) COPY_ROOT="${1#--copy-root=}"; shift ;;
    --source-env) SOURCE_ENV="$2"; shift 2 ;;
    --source-env=*) SOURCE_ENV="${1#--source-env=}"; shift ;;
    --pg-bin) PG_BIN="$2"; shift 2 ;;
    --pg-bin=*) PG_BIN="${1#--pg-bin=}"; shift ;;
    --stage) STAGE="$2"; shift 2 ;;
    --stage=*) STAGE="${1#--stage=}"; shift ;;
    --dump-file) DUMP_FILE="$2"; shift 2 ;;
    --dump-file=*) DUMP_FILE="${1#--dump-file=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1（用 --help 看用法）" ;;
  esac
done

case "$STAGE" in
  all|dump|restore) ;;
  *) die "--stage 只接受 all/dump/restore，收到：$STAGE" ;;
esac

START_TS="${EPOCHREALTIME:-$SECONDS}"

# 副本根默认按脚本位置推导（与 ps1 同款：上两级目录）
if [ -z "$COPY_ROOT" ]; then COPY_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; fi
COPY_ROOT="$(realpath -m "$COPY_ROOT")"
if [ -z "$PG_BIN" ]; then PG_BIN="$COPY_ROOT/runtime/postgresql16/lib/postgresql/16/bin"; fi
TMP_DIR="$COPY_ROOT/TMP"

# 隔离边界：只允许对带隔离标志的副本灌库，且源/副本路径不得重叠。
[ -f "$COPY_ROOT/ISOLATED_COPY.flag" ] || die "副本缺 ISOLATED_COPY.flag，拒绝灌库：$COPY_ROOT"
if [ -n "$MAIN_REPO" ]; then
  main_real="$(realpath -m "$MAIN_REPO")"
  overlap=0
  [ "$COPY_ROOT" = "$main_real" ] && overlap=1
  case "$COPY_ROOT" in "$main_real"/*) overlap=1 ;; esac
  case "$main_real" in "$COPY_ROOT"/*) overlap=1 ;; esac
  [ "$overlap" = 1 ] && die '源与副本路径重叠，拒绝操作'
fi

# dump 阶段需要生产 .env：没给 --main-repo 也没给 --source-env 时直接报错，
# 不要静默拼出一个不存在的默认路径。
case "$STAGE" in
  all|dump)
    if [ -z "$MAIN_REPO" ] && [ -z "$SOURCE_ENV" ]; then
      die '未指定生产库位置：请给 --main-repo <主仓库目录> 或 --source-env <生产 .env 路径>'
    fi
    ;;
esac
if [ -z "$SOURCE_ENV" ]; then SOURCE_ENV="$MAIN_REPO/Task/my_app/backend/.env"; fi
# 快照名带时间戳，绝不覆盖已有快照（旧快照是回滚手段）。
if [ -z "$DUMP_FILE" ]; then DUMP_FILE="$TMP_DIR/myapp_snapshot_$(date +%Y%m%d_%H%M%S).dump"; fi

# 从副本 .env 读目标端口/库名（不复制口令，副本侧只有 trust 认证）
get_copy_cfg() {
  local key="$1" def="$2" f="$COPY_ROOT/.env" line v
  [ -f "$f" ] || { printf '%s' "$def"; return 0; }
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "$key="*)
        v="${line#"$key="}"
        v="$(trim "$v")"
        [ -n "$v" ] && { printf '%s' "$v"; return 0; }
        ;;
    esac
  done < "$f"
  printf '%s' "$def"
}

TGT_PORT="$(get_copy_cfg 'PGPORT' '55432')"
TGT_USER="$(get_copy_cfg 'MY_APP_DB_USER' 'myapp')"
TGT_DB="$(get_copy_cfg 'MY_APP_DB_NAME' 'myapp')"
case "$TGT_USER" in ''|*[!A-Za-z0-9_]*) die '副本 .env 的 MY_APP_DB_USER 只允许字母数字下划线' ;; esac
case "$TGT_DB" in ''|*[!A-Za-z0-9_]*) die '副本 .env 的 MY_APP_DB_NAME 只允许字母数字下划线' ;; esac
case "$TGT_PORT" in ''|*[!0-9]*) die "副本 .env 的 PGPORT 不是数字：$TGT_PORT" ;; esac
[ "$TGT_PORT" != 5432 ] || die '副本 PGPORT 不得用生产端口 5432，请在副本 .env 改到隔离端口'

# 只解 %XX 序列（ps1 的 UnescapeDataString 同款语义）；原串里的反斜杠不动。
urldecode() {
  local s
  s="$(printf '%s' "$1" | sed -E 's/%([0-9A-Fa-f]{2})/\\x\1/g')"
  printf '%b' "$s"
}

do_dump() {
  step '=== 阶段 dump：从生产 PG 导出 ==='
  [ -f "$SOURCE_ENV" ] || die "找不到生产 .env：$SOURCE_ENV"
  local url='' line v
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      'DATABASE_URL='*)
        v="${line#'DATABASE_URL='}"
        v="$(trim "$v")"
        case "$v" in
          '"*"') v="${v#\"}"; v="${v%\"}" ;;
          "'*'") v="${v#\'}"; v="${v%\'}"; ;;
        esac
        v="$(trim "$v")"
        url="$v"
        break
        ;;
    esac
  done < "$SOURCE_ENV"
  [ -n "$url" ] || die "SourceEnv 里没有 DATABASE_URL：$SOURCE_ENV"
  # 只解析，不打印：$url 含明文口令，任何输出都不得带上它（含报错信息）
  local url_re='^postgresql(\+[A-Za-z0-9]+)?://([^:/@]+)(:([^@]*))?@([^:/]+)(:([0-9]+))?/([^?]+)'
  [[ "$url" =~ $url_re ]] || die 'DATABASE_URL 格式无法解析（期望 postgresql://user:pass@host:port/db）'
  local src_user="${BASH_REMATCH[2]}"
  local src_pass_raw="${BASH_REMATCH[4]:-}"
  local src_host="${BASH_REMATCH[5]}"
  local src_port="${BASH_REMATCH[7]:-5432}"
  local src_db="${BASH_REMATCH[8]}"
  local src_pass
  src_pass="$(urldecode "$src_pass_raw")"
  local pg_dump_bin="$PG_BIN/pg_dump"
  [ -x "$pg_dump_bin" ] || die "缺 pg_dump：$pg_dump_bin（用 --pg-bin 指定 PostgreSQL 16 的 bin）"
  step "源库：$src_host:$src_port/$src_db（连接串含口令，不打印）"
  if [ "$DRY_RUN" = 1 ]; then step "<干跑> pg_dump -Fc -> $DUMP_FILE"; return 0; fi
  [ ! -e "$DUMP_FILE" ] || die "快照已存在，拒绝覆盖：$DUMP_FILE（请换 --dump-file 或先移走旧快照）"
  local dump_dir
  dump_dir="$(dirname "$DUMP_FILE")"
  [ -d "$dump_dir" ] || mkdir -p "$dump_dir"
  # 口令只在子 shell 里经 PGPASSWORD 传给 pg_dump/psql，子 shell 退出即清除；
  # search_path 写进 sidecar 的动作也在子 shell 内完成，失败则不留 sidecar。
  (
    export PGPASSWORD="$src_pass"
    "$pg_dump_bin" -Fc -h "$src_host" -p "$src_port" -U "$src_user" -d "$src_db" -f "$DUMP_FILE" 2>&1 | sed 's/^/  /'
    local sp
    sp="$("$PG_BIN/psql" -w -h "$src_host" -p "$src_port" -U "$src_user" -d "$src_db" -tAc 'SHOW search_path' 2>/dev/null || true)"
    sp="$(trim "$sp")"
    [ -n "$sp" ] || sp='app, eflow, lte_pl, battery, ms_sla, fw, ipran_ume, ran_ume, public'
    printf '%s' "$sp" > "$DUMP_FILE.searchpath"
  )
  local rc=$?
  src_pass=''
  [ "$rc" = 0 ] || die "pg_dump 失败（exit=$rc）"
  local bytes size_mb sp
  bytes="$(stat -c %s "$DUMP_FILE" 2>/dev/null || echo 0)"
  size_mb="$(awk -v b="$bytes" 'BEGIN { printf "%.1f", b/1048576 }')"
  ok "快照已导出：$DUMP_FILE（${size_mb} MB）"
  sp="$(cat "$DUMP_FILE.searchpath" 2>/dev/null || true)"
  ok "已记录源库 search_path：$sp"
}

do_restore() {
  step '=== 阶段 restore：灌入副本便携 PG ==='
  if [ "$DRY_RUN" != 1 ] && [ ! -f "$DUMP_FILE" ]; then die "找不到快照：$DUMP_FILE（先跑 --stage dump）"; fi
  [ -d "$COPY_ROOT" ] || die "副本根目录不存在：$COPY_ROOT"
  local bin="$COPY_ROOT/runtime/postgresql16/lib/postgresql/16/bin"
  local pgdata="$COPY_ROOT/runtime/pgdata"
  local initdb="$bin/initdb" pg_ctl="$bin/pg_ctl" psql="$bin/psql" createdb="$bin/createdb" pg_restore="$bin/pg_restore"
  local exe
  for exe in "$initdb" "$pg_ctl" "$psql" "$createdb" "$pg_restore"; do
    [ -x "$exe" ] || die "副本缺 PG 工具：$exe（请先跑 -Stage runtimes）"
  done

  local pg_log="$TMP_DIR/portable-pg.log"
  if [ ! -f "$pgdata/PG_VERSION" ]; then
    step "首次恢复，initdb -> $pgdata"
    if [ "$DRY_RUN" = 1 ]; then
      step '<干跑> 跳过 initdb'
    else
      [ -d "$TMP_DIR" ] || mkdir -p "$TMP_DIR"
      "$initdb" -D "$pgdata" -U "$TGT_USER" -A trust --encoding=UTF8 >"$TMP_DIR/portable-initdb.out" 2>&1 || die 'initdb 失败'
      sed 's/^/  /' "$TMP_DIR/portable-initdb.out"
    fi
  fi
  if [ "$DRY_RUN" = 1 ]; then
    step "<干跑> pg_ctl start / createdb / pg_restore（目标 127.0.0.1:$TGT_PORT/$TGT_DB）"
    return 0
  fi

  [ -d "$TMP_DIR" ] || mkdir -p "$TMP_DIR"
  local rc=0
  if ! "$pg_ctl" -D "$pgdata" status >/dev/null 2>&1; then
    # 不能用管道捕获输出：pg_ctl 拉起的 postgres 会继承管道写句柄，
    # 管道永不 EOF，脚本会永久挂住（start.sh 同款写法，输出落文件）。
    local sock_dir="$TMP_DIR/pgsock"
    [ -d "$sock_dir" ] || mkdir -p "$sock_dir"
    local ctl_out="$TMP_DIR/portable-pg_ctl-restore.out"
    local ctl_err="$TMP_DIR/portable-pg_ctl-restore.err"
    "$pg_ctl" -D "$pgdata" -l "$pg_log" -o "-p $TGT_PORT -h 127.0.0.1 -k $sock_dir" start >"$ctl_out" 2>"$ctl_err" \
      || die "pg_ctl start 未能启动，见 $ctl_err"
  fi
  # 端口 LISTEN != 可连接，轮询 SELECT 1 到真正 ready
  local ready=0 i probe
  for i in $(seq 1 60); do
    if probe="$("$psql" -w -h 127.0.0.1 -p "$TGT_PORT" -U "$TGT_USER" -d postgres -tAc 'SELECT 1' 2>/dev/null)"; then
      [ "$(printf '%s' "$probe" | tr -d '[:space:]')" = '1' ] && { ready=1; break; }
    fi
    sleep 0.5
  done
  [ "$ready" = 1 ] || die "便携 PG 未在 30s 内进入可连接状态，见 $pg_log"
  # 写入前确认连上的就是副本 pgdata 实例（防连到本机别的 PG，含生产）。
  local data_dir exp_dir
  data_dir="$("$psql" -w -h 127.0.0.1 -p "$TGT_PORT" -U "$TGT_USER" -d postgres -tAc 'SHOW data_directory' 2>/dev/null || true)"
  data_dir="$(trim "$data_dir")"
  data_dir="$(realpath -m "$data_dir")"
  exp_dir="$(realpath -m "$pgdata")"
  [ "$data_dir" = "$exp_dir" ] || die "便携 PG data_directory 不符（实际 '$data_dir'，期望 '$pgdata'），拒绝灌库"

  local exists
  exists="$("$psql" -w -h 127.0.0.1 -p "$TGT_PORT" -U "$TGT_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$TGT_DB'" 2>/dev/null || true)"
  if [ -z "$(printf '%s' "$exists" | tr -d '[:space:]')" ]; then
    "$createdb" -h 127.0.0.1 -p "$TGT_PORT" -U "$TGT_USER" "$TGT_DB" 2>&1 | sed 's/^/  /' || rc=$?
    [ "${rc:-0}" = 0 ] || die "createdb 失败（exit=$rc）"
  else
    # 目标库已存在：导入前先一致备份（回滚手段），备份失败则拒绝继续。
    local backup_file="$TMP_DIR/_pre_restore_$(date +%Y%m%d_%H%M%S).dump"
    step "目标库已存在，先备份 -> $backup_file"
    "$bin/pg_dump" -Fc -h 127.0.0.1 -p "$TGT_PORT" -U "$TGT_USER" -d "$TGT_DB" -f "$backup_file" 2>&1 | sed 's/^/  /' || rc=$?
    [ "${rc:-0}" = 0 ] || die "导入前备份失败（exit=$rc），拒绝继续以免丢失副本现有数据"
    ok "已备份副本现有库：$backup_file"
  fi
  # --single-transaction --exit-on-error：要么整体成功要么整体回滚，绝不灌半套数据。
  "$pg_restore" --clean --if-exists --single-transaction --exit-on-error --no-owner --no-privileges \
    -h 127.0.0.1 -p "$TGT_PORT" -U "$TGT_USER" -d "$TGT_DB" "$DUMP_FILE" 2>&1 | sed 's/^/  /' || rc=$?
  [ "${rc:-0}" = 0 ] || die "pg_restore 失败（exit=$rc），事务已回滚；副本原有数据仍在导入前备份中"

  # 补回 role 级 search_path：pg_dump 不含 cluster 级 role 配置，缺了它
  # backend 会连得上库却找不到 app.eflow_tt / eflow.*，页面仍报 500/503。
  local sp_file="$DUMP_FILE.searchpath" sp=''
  [ -f "$sp_file" ] && sp="$(trim "$(cat "$sp_file" 2>/dev/null || true)")"
  [ -n "$sp" ] || sp='app, eflow, lte_pl, battery, ms_sla, fw, ipran_ume, ran_ume, public'
  # search_path 会被拼进 ALTER ROLE，只允许 schema 标识符字符，杜绝注入。
  local sp_re='^[A-Za-z0-9_, "$]+$'
  [[ "$sp" =~ $sp_re ]] || die "search_path 含非法字符，拒绝设置：$sp"
  "$psql" -w -h 127.0.0.1 -p "$TGT_PORT" -U "$TGT_USER" -d postgres -tAc "ALTER ROLE $TGT_USER SET search_path = $sp" 2>&1 | sed 's/^/  /' || rc=$?
  [ "${rc:-0}" = 0 ] || die "设置 role search_path 失败（exit=$rc）"
  ok "已设置副本 role search_path = $sp"

  # 生产库业务表分散在多个 schema，只数 public 会恒为 0、误导验收
  local tables by_schema
  tables="$("$psql" -w -h 127.0.0.1 -p "$TGT_PORT" -U "$TGT_USER" -d "$TGT_DB" -tAc "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')" 2>/dev/null || true)"
  by_schema="$("$psql" -w -h 127.0.0.1 -p "$TGT_PORT" -U "$TGT_USER" -d "$TGT_DB" -tAc "SELECT schemaname || ':' || count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') GROUP BY schemaname ORDER BY 1" 2>/dev/null || true)"
  ok "恢复完成：业务表共 $(trim "$tables") 张"
  step "分 schema：$(printf '%s' "$by_schema" | tr '\n' ' ' | sed -e 's/  */ /g' -e 's/^ *//' -e 's/ *$//')"
}

case "$STAGE" in
  all) do_dump; do_restore ;;
  dump) do_dump ;;
  restore) do_restore ;;
esac

END_TS="${EPOCHREALTIME:-$SECONDS}"
ELAPSED="$(awk -v s="$START_TS" -v e="$END_TS" 'BEGIN { printf "%.1f", e - s }')"
DRY_SUFFIX=''
[ "$DRY_RUN" = 1 ] && DRY_SUFFIX='（干跑）'
ok "bundle_data 完成（$STAGE），耗时 ${ELAPSED} 秒${DRY_SUFFIX}"
