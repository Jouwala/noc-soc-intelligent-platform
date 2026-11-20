#!/bin/sh
# start.sh —— 离线副本一键启动（Linux，ZIP 交付 Phase 1，P1-4）
# =============================================================================
# 对标 ops/deploy/start_all.ps1：同一套六步 + pid 文件 + 熔断 + 干跑。
# 入口：副本根目录的 start.sh（薄壳，只调本脚本）。
# 真相源：副本根 .env（模板 ops/deploy/env_template.txt）；端口与路径都不写死。
#
# 与生产启动脚本的区别（与 .ps1 版一致，别照抄生产脚本）：
#   · 副本不用 watchdog / worker 组 / 开机自启——MY_APP_DISABLE_SCHEDULER=1
#     且不设 MY_APP_TASK_QUEUE，否则采集任务会连生产上游、重复投递告警。
#   · 副本直接起 uvicorn / vite preview / tsx server，进程由本脚本用 pid 文件
#     托管；stop.sh 只按 pid 文件停，绝不按端口盲杀（源机可能正在跑生产）。
#   · 解释器一律用副本自带的 runtime/ 下的 python312 / node，路径由 $ROOT 推出。
# P1-6 外部目录：设了进程环境变量 NOC_HOME 后，配置/数据/日志/运行时一律走
# 部署根下的外部目录（解析见同目录 noc-paths.sh），版本目录在启动后零写入；
# 不设 NOC_HOME 时全部路径与旧行为一致。
# Linux 差异（相对 .ps1 版）：子进程用 trap '' HUP + 重定向脱离终端（nohup
# 等价）；端口探测用副本 Python 的 socket 直连；nginx 配置展开用内置的
# awk 展开器（同一模板、同一占位符集，见 expand_nginx_conf 注释）。
# 用法：
#   ./ops/deploy/start.sh [--dry-run] [--skip-nginx-conf]
# =============================================================================
set -u
trap '' HUP
START_S=$(date +%s)
DRY_RUN=0
SKIP_NGINX_CONF=0
for _a in "$@"; do
  case "$_a" in
    --dry-run) DRY_RUN=1 ;;
    --skip-nginx-conf) SKIP_NGINX_CONF=1 ;;
    --help|-h) echo "用法: $0 [--dry-run] [--skip-nginx-conf]"; exit 0 ;;
    *) echo "[FAIL] 未知参数: $_a" >&2; exit 2 ;;
  esac
done

step() { echo "[*] $1"; }
ok() { echo "[OK] $1"; }
warn() { echo "[WARN] $1"; }
err() { echo "[FAIL] $1"; }
skip() { echo "[SKIP] $1"; }
die() { echo "[FAIL] $1" >&2; exit 1; }

_self=$0
case "$_self" in */*) ;; *) _self=$(command -v -- "$_self" || printf '%s' "$_self") ;; esac
SCRIPT_DIR=$(dirname "$(readlink -f -- "$_self")")
ROOT=$(dirname "$(dirname "$SCRIPT_DIR")")
NOC_PATHS_LIB="$SCRIPT_DIR/noc-paths.sh"
[ -f "$NOC_PATHS_LIB" ] || die "缺少目录解析脚本: $NOC_PATHS_LIB（请从仓库重新取 ops/deploy/）"
# shellcheck disable=SC1091
. "$NOC_PATHS_LIB"
noc_paths "$ROOT"
[ "$NP_MODE" = "external" ] && { noc_assert_outside_root || exit 1; }
TMP_DIR=$NP_TMP_DIR
RUNTIME_DIR=$NP_RUNTIME_DIR
PY_EXE=$NP_PY_EXE
NODE_EXE=$NP_NODE_EXE
PG_BIN=$NP_PG_BIN
NGINX_DIR=$NP_NGINX_DIR
NGINX_EXE=$NP_NGINX_EXE
MYAPP_DIR="$ROOT/apps/my_app"
FE_DIR="$MYAPP_DIR/frontend"
BOT_DIR="$ROOT/apps/whatsapp-kb-bot"
PORTAL_ROOT="$ROOT/apps/portal"
[ -d "$PORTAL_ROOT" ] || PORTAL_ROOT=""

# 防误跑：本脚本只应在 build 出来的副本里执行（与 .ps1 版同一道检查）。
[ -f "$NODE_EXE" ] && [ -f "$MYAPP_DIR/backend/main.py" ] || die "当前目录不是有效副本（缺 $NODE_EXE 或 apps/my_app/backend/main.py）：$ROOT
本脚本只能在 build 出来的副本或 ZIP 部署里运行（外部模式请检查 NOC_HOME/NOC_RUNTIME_DIR）。"

[ "$DRY_RUN" != "1" ] && [ ! -d "$TMP_DIR" ] && mkdir -p "$TMP_DIR"

# ---------------------------------------------------------------- .env 载入
# 隔离原则：副本 .env 是唯一配置真相源，优先于继承的进程/系统环境变量。
# cfg_get 语义与 .ps1 的 Get-Cfg 一致：覆盖文件 > .env 文件 > 进程环境 > 默认。
# .env 解析与 Import-DotEnv 一致：去首尾空白、跳过空行/# 行、按第一个 '='
# 切分、不去引号、不处理 export；重复键以后出现的为准。
CFG_FILE=$NP_ENV_FILE
if [ "$DRY_RUN" = "1" ]; then CFG_OVERRIDES=$(mktemp /tmp/noc-cfg-XXXXXX); trap 'rm -f "$CFG_OVERRIDES"' EXIT INT TERM;
else CFG_OVERRIDES="$TMP_DIR/cfg-overrides.env"; fi
: > "$CFG_OVERRIDES" 2>/dev/null || CFG_OVERRIDES=$(mktemp /tmp/noc-cfg-XXXXXX)
ENV_TPL="$SCRIPT_DIR/env_template.txt"
if [ ! -f "$CFG_FILE" ]; then
  [ -f "$ENV_TPL" ] || die "缺少 .env 模板: $ENV_TPL"
  if [ "$DRY_RUN" != "1" ]; then
    _env_parent=$(dirname "$CFG_FILE")
    [ -d "$_env_parent" ] || mkdir -p "$_env_parent"
    cp "$ENV_TPL" "$CFG_FILE"
    step "首次启动：已从模板生成 $CFG_FILE"
  else
    # #192：干跑不写文件，别报「已生成」
    step "<干跑> 首次启动将从模板生成 $CFG_FILE（本次不写文件）"
  fi
fi
_cfg_lookup() {
  [ -f "$2" ] || return 1
  grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | tail -n 1 | cut -d= -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}
cfg_get() {
  _cg_v=$(_cfg_lookup "$1" "$CFG_OVERRIDES") || _cg_v=""
  [ -n "$_cg_v" ] && { printf '%s' "$_cg_v"; return 0; }
  _cg_v=$(_cfg_lookup "$1" "$CFG_FILE") || _cg_v=""
  [ -n "$_cg_v" ] && { printf '%s' "$_cg_v"; return 0; }
  _cg_v=$(printenv "$1" 2>/dev/null || true)
  [ -n "$_cg_v" ] && { printf '%s' "$_cg_v"; return 0; }
  printf '%s' "$2"
}
cfg_force() { printf '%s=%s\n' "$1" "$2" >> "$CFG_OVERRIDES"; }
set_cfg() { export "$1=$2"; }

HTTP_PORT=$(cfg_get 'NGINX_HTTP_PORT' '8080')
HTTPS_PORT=$(cfg_get 'NGINX_HTTPS_PORT' '8443')
BE_PORT=$(cfg_get 'MY_APP_BE_PORT' '7039')
FE_PORT=$(cfg_get 'MY_APP_FE_PORT' '7091')
PORTAL_PORT=$(cfg_get 'PORTAL_PORT' '9277')
PG_PORT=$(cfg_get 'PGPORT' '55432')
QA_PORT=$(cfg_get 'QA_HTTP_PORT' '9279')
WA_ANALYSIS_PORT=$(cfg_get 'WA_ANALYSIS_PORT' '9278')
FMS_PROXY_PORT=$(cfg_get 'MY_APP_PROXY_FMS_PORT' '8887')
VMAX_PROXY_PORT=$(cfg_get 'MY_APP_PROXY_VMAX_PORT' '8890')
PG_USER=$(cfg_get 'MY_APP_DB_USER' 'myapp')
PG_DB=$(cfg_get 'MY_APP_DB_NAME' 'myapp')
for _p in "$HTTP_PORT" "$HTTPS_PORT" "$BE_PORT" "$FE_PORT" "$PORTAL_PORT" "$PG_PORT" "$QA_PORT" "$WA_ANALYSIS_PORT" "$FMS_PROXY_PORT" "$VMAX_PROXY_PORT"; do
  case "$_p" in ''|*[!0-9]*) die "端口配置不是数字: $_p（请检查 $CFG_FILE）" ;; esac
done
case "$PG_USER" in ''|*[!A-Za-z0-9_]*) die 'MY_APP_DB_USER 只允许字母数字下划线（会被拼进 psql 命令）' ;; esac
case "$PG_DB" in ''|*[!A-Za-z0-9_]*) die 'MY_APP_DB_NAME 只允许字母数字下划线（会被拼进 psql 命令）' ;; esac
# 隔离熔断：副本端口绝不允许落在生产端口上，且彼此不得重复。
PROD_PORTS=" 80 443 7038 7090 9267 9268 9269 5432 8787 8790 "
_seen=" "
for _p in "$HTTP_PORT" "$HTTPS_PORT" "$BE_PORT" "$FE_PORT" "$PORTAL_PORT" "$PG_PORT" "$QA_PORT" "$WA_ANALYSIS_PORT" "$FMS_PROXY_PORT" "$VMAX_PROXY_PORT"; do
  case "$PROD_PORTS" in *" $_p "*) die "副本端口 $_p 与生产端口冲突，请在副本 .env 改到隔离端口后再启动" ;; esac
  case "$_seen" in *" $_p "*) die "副本端口配置有重复: $_p" ;; esac
  _seen="$_seen$_p "
done

# ------------------------------------------------------- 派生变量（与 .ps1 同表）
PGDATA=$NP_PGDATA
FE_DIST="$ROOT/apps/_fe_dist"
AUTH_DIR=$NP_AUTH_DIR
[ "$DRY_RUN" != "1" ] && [ ! -d "$AUTH_DIR" ] && mkdir -p "$AUTH_DIR"
# 便携 PG 用 trust 认证且只绑回环，连接串里刻意没有口令。
DB_URL="postgresql://$PG_USER@127.0.0.1:$PG_PORT/$PG_DB"
set_cfg 'PGDATA' "$PGDATA"
set_cfg 'FE_DIST_OUT' "$FE_DIST"
set_cfg 'PORTAL_AUTH_DATA_DIR' "$AUTH_DIR"
set_cfg 'PORTAL_QA_NODE' "$NODE_EXE"
set_cfg 'PORTAL_QA_PORT' "$QA_PORT"
set_cfg 'PORTAL_QA_HOST' '127.0.0.1'
set_cfg 'QA_HTTP_HOST' '127.0.0.1'
set_cfg 'QA_HTTP_PORT' "$QA_PORT"
set_cfg 'MY_APP_API_BASE' "http://127.0.0.1:$BE_PORT"
set_cfg 'MY_APP_BACKEND_PORT' "$BE_PORT"
set_cfg 'MY_APP_DB_MODE' 'pg'
set_cfg 'DATABASE_URL' "$DB_URL"
set_cfg 'PORT' "$PORTAL_PORT"
set_cfg 'PORTAL_HOST' '127.0.0.1'
set_cfg 'PGHOSTADDR' '127.0.0.1'
set_cfg 'PGPORT' "$PG_PORT"
set_cfg 'MY_APP_PROXY_FMS_PORT' "$FMS_PROXY_PORT"
set_cfg 'MY_APP_PROXY_VMAX_PORT' "$VMAX_PROXY_PORT"
set_cfg 'MY_APP_PROXY_PY' "$PY_EXE"
set_cfg 'MY_APP_REPORT_PY' "$PY_EXE"
set_cfg 'EFLOW_QA_DIR' "$ROOT/apps/eflow_Q&A"
if [ "$NP_MODE" = "external" ]; then
  _bot_data=$NP_BOT_DATA_DIR
  set_cfg 'PORTAL_DEFAULT_FILE' "$NP_DATA_DIR/portal-default.json"
  set_cfg 'TT_PYTHON' "$PY_EXE"
  set_cfg 'CHAT_CA_FILE' "$NP_CERTS_DIR/liberia-noc-ca.pem"
  set_cfg 'INDEX_PATH' "$_bot_data/index.json"
  set_cfg 'LOOKUPS_PATH' "$_bot_data/lookups.json"
  set_cfg 'AUTH_DIR' "$_bot_data/auth"
  set_cfg 'QA_LOG_PATH' "$_bot_data/qa.sqlite"
  set_cfg 'NOTIFY_AUTH_DIR' "$_bot_data/auth-notify"
  set_cfg 'MYAPP_LOG_DIR' "$NP_LOGS_DIR"
  set_cfg 'MY_APP_DB_PATH' "$NP_DATA_DIR/app.db"
  set_cfg 'MW_LINKS_PUBLIC_DIR' 'off'
  set_cfg 'BOT_LOG_DIR' "$NP_LOGS_DIR/bot"
fi

# 安全熔断：副本必须禁用调度器，不允许被 .env 改回 0
if [ "$(cfg_get 'MY_APP_DISABLE_SCHEDULER' '1')" != "1" ]; then warn '.env 里 MY_APP_DISABLE_SCHEDULER != 1，已强制改回 1（副本禁止连生产采集上游）'; fi
cfg_force 'MY_APP_DISABLE_SCHEDULER' '1'
set_cfg 'MY_APP_DISABLE_SCHEDULER' '1'
unset MY_APP_TASK_QUEUE || true
# 副本绝不接管真实 WhatsApp 会话/投递：即使 .env 误设 1 也强制关。
cfg_force 'START_WA_BOT' '0'
set_cfg 'START_WA_BOT' '0'
unset PYTHONPATH PYTHONHOME NODE_PATH || true
# 子进程优先用副本自带解释器。
PATH="$(dirname "$NODE_EXE"):$(dirname "$PY_EXE"):$PATH"
export PATH

# backend/.env 只写本地 trust 连接串，不含任何真实口令；已存在则不覆盖。
# 外部模式不写（连接串已由进程环境携带；往版本目录写文件会破坏零写入）。
BACKEND_ENV="$MYAPP_DIR/backend/.env"
if [ "$NP_MODE" = "external" ]; then
  step '外部模式：跳过 backend/.env 生成（连接串已由进程环境携带）'
elif [ ! -f "$BACKEND_ENV" ]; then
  if [ "$DRY_RUN" != "1" ]; then printf 'MY_APP_DB_MODE=pg\nDATABASE_URL=%s\nMY_APP_DISABLE_SCHEDULER=1\n' "$DB_URL" > "$BACKEND_ENV"; fi
  if [ "$DRY_RUN" = "1" ]; then step "<干跑> 将生成 $BACKEND_ENV（仅本地回环连接，无口令）";
  else step "已生成 $BACKEND_ENV（仅本地回环连接，无口令）"; fi
fi

echo ''
echo '==================== 目录规划 ===================='
echo "  模式       $NP_MODE"
echo "  版本目录   $ROOT"
[ "$NP_MODE" = "external" ] && echo "  部署根     $NP_NOC_HOME"
echo "  配置       $NP_ENV_FILE"
echo "  数据       $NP_DATA_DIR"
echo "  日志       $NP_LOGS_DIR"
echo "  运行时     $NP_RUNTIME_DIR"
echo "  PG 数据    $PGDATA"
echo "  nginx 配置 $NP_NGINX_CONF_OUT"
echo ''

# ------------------------------------------------------------------ 通用工具
port_listening() {
  "$PY_EXE" -c 'import socket,sys; s=socket.socket(); s.settimeout(1.2); r=s.connect_ex(("127.0.0.1", int(sys.argv[1]))); s.close(); sys.exit(0 if r==0 else 1)' "$1" 2>/dev/null
}
wait_port() {
  _wp_end=$(( $(date +%s) + $2 ))
  while [ "$(date +%s)" -lt "$_wp_end" ]; do
    port_listening "$1" && return 0
    sleep 0.5
  done
  return 1
}
pid_file() { printf '%s' "$TMP_DIR/portable-$1.pid"; }
proc_starttime() {
  _ps_stat=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
  _ps_after=${_ps_stat##*) }
  # shellcheck disable=SC2086
  set -- $_ps_after
  printf '%s' "${20}"
}
_proc_gone() {
  _pg_pid=$1
  [ -d "/proc/$_pg_pid" ] || return 0
  _pg_stat=$(cat "/proc/$_pg_pid/stat" 2>/dev/null) || return 0
  _pg_after=${_pg_stat##*) }
  # shellcheck disable=SC2086
  set -- $_pg_after
  [ "$1" = "Z" ]
}
save_pid() {
  _sp_comm=$(cat "/proc/$2/comm" 2>/dev/null || true)
  _sp_start=$(proc_starttime "$2" || true)
  [ -n "$_sp_comm" ] && [ -n "$_sp_start" ] || return 0
  printf '%s|%s|%s' "$2" "$_sp_comm" "$_sp_start" > "$(pid_file "$1")"
}
# 端口已被占用时判定是否本副本上一轮已起过：是则跳过，外部占用则拒绝启动。
own_port() {
  port_listening "$2" || return 1
  _op_pf=$(pid_file "$1")
  if [ -f "$_op_pf" ]; then
    _op_raw=$(cat "$_op_pf" 2>/dev/null || true)
    _op_pid=${_op_raw%%|*}; _op_rest=${_op_raw#*|}; _op_comm=${_op_rest%%|*}; _op_ticks=${_op_rest#*|}
    case "$_op_pid" in ''|*[!0-9]*) ;; *)
      if ! _proc_gone "$_op_pid"; then
        _op_now_comm=$(cat "/proc/$_op_pid/comm" 2>/dev/null || true)
        _op_now_ticks=$(proc_starttime "$_op_pid" || true)
        if [ "$_op_now_comm" = "$_op_comm" ] && [ "$_op_now_ticks" = "$_op_ticks" ]; then
          _op_exe=$(readlink "/proc/$_op_pid/exe" 2>/dev/null || true)
          if [ -n "$_op_exe" ] && { np_path_under "$ROOT" "$_op_exe" || np_path_under "$RUNTIME_DIR" "$_op_exe"; }; then
            skip "$1 已由本副本启动（pid=$_op_pid），端口 $2 不重复拉起"
            return 0
          fi
        fi
      fi
      ;;
    esac
  fi
  die "端口 $2 被外部进程占用（非本副本 $1），拒绝启动以免抢占生产服务。请先释放该端口或改副本 .env。"
}
# 启动一个守护进程：$1 名 $2 exe $3 cwd $4 端口(0=不等) $5 等待秒，其余为参数。
start_detached() {
  _sd_name=$1; _sd_exe=$2; _sd_work=$3; _sd_port=$4; _sd_wait=$5; shift 5
  if [ "$_sd_port" -gt 0 ] && own_port "$_sd_name" "$_sd_port"; then return 0; fi
  if [ ! -x "$_sd_exe" ]; then err "$_sd_name 缺解释器: $_sd_exe"; return 1; fi
  if [ ! -d "$_sd_work" ]; then err "$_sd_name 缺工作目录: $_sd_work"; return 1; fi
  if [ "$DRY_RUN" = "1" ]; then step "<干跑> $_sd_name : \"$_sd_exe\" $*   (cwd=$_sd_work)"; return 0; fi
  _sd_out="$TMP_DIR/portable-$_sd_name.out"
  _sd_err="$TMP_DIR/portable-$_sd_name.err"
  # 子 shell + exec：$! 即守护进程自身 pid；HUP 已在脚本顶部 trap 忽略，
  # 子进程继承忽略态 + 重定向，启动器退出后继续存活。
  (cd "$_sd_work" && exec "$_sd_exe" "$@" >>"$_sd_out" 2>>"$_sd_err" < /dev/null) &
  _sd_pid=$!
  sleep 0.2
  if _proc_gone "$_sd_pid"; then err "$_sd_name 启动后立即退出，日志：$_sd_out / $_sd_err"; return 1; fi
  save_pid "$_sd_name" "$_sd_pid"
  if [ "$_sd_port" -gt 0 ]; then
    if wait_port "$_sd_port" "$_sd_wait"; then ok "$_sd_name 已监听 $_sd_port (pid=$_sd_pid)"; return 0; fi
    err "$_sd_name 未在 ${_sd_wait}s 内监听 $_sd_port，日志：$_sd_out / $_sd_err"
    return 1
  fi
  ok "$_sd_name 已启动 (pid=$_sd_pid)"
  return 0
}
# nginx 配置展开：与 write_nginx_conf.ps1 同一模板、同一占位符集、同一断言。
# 占位符：{{ROOT}} {{HTTP_PORT}} {{HTTPS_PORT}} {{BE_PORT}} {{FE_PORT}}
# {{PORTAL_PORT}} {{NGINX_LOGS}} {{NGINX_TEMP}} {{NGINX_CERTS}}。
# 暂不抽象为共享实现的原因：Linux 目标机无 PowerShell，.ps1 展开器无法复用；
# 契约以模板文件与本注释的占位符表为准，两处改动须同步（AGENTS.md 三.7）。
# 替换用 awk 的 index/substr 字面替换（无正则、无转义问题，与 .NET String.Replace 等价）。
expand_nginx_conf() {
  _ec_tpl="$ROOT/nginx.conf.template"
  _ec_out=$NP_NGINX_CONF_OUT
  [ -f "$_ec_tpl" ] || die "缺少模板: $_ec_tpl"
  [ -x "$NGINX_EXE" ] || die "缺少 nginx: $NGINX_DIR（请先备齐运行时）"
  mkdir -p "$NP_NGINX_LOGS_DIR" "$NP_NGINX_TEMP_DIR" "$(dirname "$_ec_out")"
  NOC_TPL_ROOT="$ROOT" NOC_TPL_HTTP="$HTTP_PORT" NOC_TPL_HTTPS="$HTTPS_PORT" \
  NOC_TPL_BE="$BE_PORT" NOC_TPL_FE="$FE_PORT" NOC_TPL_PORTAL="$PORTAL_PORT" \
  NOC_TPL_LOGS="$NP_NGINX_LOGS_DIR" NOC_TPL_TEMP="$NP_NGINX_TEMP_DIR" NOC_TPL_CERTS="$NP_CERTS_DIR" \
  awk '
    function rep(s, pat, val,    out, i) {
      out = ""
      while ((i = index(s, pat)) > 0) { out = out substr(s, 1, i-1) val; s = substr(s, i + length(pat)) }
      return out s
    }
    BEGIN {
      r=ENVIRON["NOC_TPL_ROOT"]; h=ENVIRON["NOC_TPL_HTTP"]; s=ENVIRON["NOC_TPL_HTTPS"];
      b=ENVIRON["NOC_TPL_BE"]; f=ENVIRON["NOC_TPL_FE"]; p=ENVIRON["NOC_TPL_PORTAL"];
      l=ENVIRON["NOC_TPL_LOGS"]; t=ENVIRON["NOC_TPL_TEMP"]; c=ENVIRON["NOC_TPL_CERTS"];
    }
    { $0 = rep($0, "{{ROOT}}", r); $0 = rep($0, "{{HTTP_PORT}}", h); $0 = rep($0, "{{HTTPS_PORT}}", s);
      $0 = rep($0, "{{BE_PORT}}", b); $0 = rep($0, "{{FE_PORT}}", f); $0 = rep($0, "{{PORTAL_PORT}}", p);
      $0 = rep($0, "{{NGINX_LOGS}}", l); $0 = rep($0, "{{NGINX_TEMP}}", t); $0 = rep($0, "{{NGINX_CERTS}}", c); print }
  ' "$_ec_tpl" > "$_ec_out.tmp" || die "nginx 配置展开失败"
  mv "$_ec_out.tmp" "$_ec_out"
  _ec_left=$(grep -o '{{[A-Z_]*}}' "$_ec_out" | head -n 1 || true)
  [ -n "$_ec_left" ] && die "模板里还有未替换的占位符: $_ec_left"
  echo "[nginx-conf] 已生成 $_ec_out"
  echo "[nginx-conf] ROOT=$ROOT  HTTP=$HTTP_PORT  HTTPS=$HTTPS_PORT  BE=$BE_PORT  FE=$FE_PORT  PORTAL=$PORTAL_PORT"
  _ec_tout=$("$NGINX_EXE" -t -p "$NGINX_DIR/" -c "$_ec_out" 2>&1) || { echo "$_ec_tout" | sed 's/^/  /'; die "nginx 配置语法校验失败，未启动"; }
  echo "$_ec_tout" | sed 's/^/  /'
  echo '[nginx-conf] 语法校验通过'
}

# ------------------------------------------------------------- 0) 启动预检
[ -f "$ROOT/ISOLATED_COPY.flag" ] || die '缺 ISOLATED_COPY.flag：本脚本只能在便携副本里运行，拒绝在主仓库启动'
_preflight_fail=0
_preflight_check() {
  if [ ! -e "$2" ]; then
    if [ "$DRY_RUN" = "1" ]; then warn "预检缺 $1 -> $2";
    else err "启动预检失败：缺 $1 -> $2（请先补齐依赖/运行时）"; _preflight_fail=1; fi
  fi
}
_preflight_check 'Python 解释器' "$PY_EXE"
_preflight_check 'Node 解释器' "$NODE_EXE"
_preflight_check 'my_app 后端入口' "$MYAPP_DIR/backend/main.py"
_preflight_check '前端 vite' "$FE_DIR/node_modules/vite/bin/vite.js"
_preflight_check '便携 PG pg_ctl' "$PG_BIN/pg_ctl"
[ -n "$PORTAL_ROOT" ] && _preflight_check '门户 tsx' "$PORTAL_ROOT/node_modules/tsx/dist/cli.mjs"
[ "$_preflight_fail" != "0" ] && exit 1
[ "$DRY_RUN" != "1" ] && ok '启动预检通过（隔离标志 + 核心解释器/入口齐全）'

# ------------------------------------------------- 1/6 便携 PostgreSQL
step '--- 1/6 便携 PostgreSQL ---'
PG_CTL="$PG_BIN/pg_ctl"
PSQL="$PG_BIN/psql"
if [ ! -x "$PG_CTL" ]; then
  err "缺 pg_ctl: $PG_BIN（请先备齐运行时）"
elif [ "$DRY_RUN" = "1" ]; then
  step "<干跑> 跳过 initdb / pg_ctl start（$PGDATA）"
else
  if [ ! -f "$PGDATA/PG_VERSION" ]; then
    step "首次启动，initdb -> $PGDATA"
    "$PG_BIN/initdb" -D "$PGDATA" -U "$PG_USER" -A trust --encoding=UTF8 >"$TMP_DIR/portable-initdb.out" 2>&1 || die 'initdb 失败'
    sed 's/^/  /' "$TMP_DIR/portable-initdb.out"
  fi
  PG_LOG="$TMP_DIR/portable-pg.log"
  # #192：PGDG 默认 socket 目录 /var/run/postgresql 非 postgres 用户写不进，
  # 首次启动会 Permission denied。用包内 TMP 下的目录，启动时建好。
  PG_SOCK_DIR="$TMP_DIR/pgsock"
  if ! "$PG_CTL" -D "$PGDATA" status >/dev/null 2>&1; then
    # 不能用管道捕获输出：pg_ctl 拉起的 postgres 会继承管道写句柄，
    # 管道永远等不到 EOF（见 .ps1 版注释）。改成输出重定向到文件。
    [ -d "$PG_SOCK_DIR" ] || mkdir -p "$PG_SOCK_DIR"
    "$PG_CTL" -D "$PGDATA" -l "$PG_LOG" -o "-p $PG_PORT -h 127.0.0.1 -k $PG_SOCK_DIR" start >"$TMP_DIR/portable-pg_ctl.out" 2>"$TMP_DIR/portable-pg_ctl.err" || die "pg_ctl start 失败，见 $TMP_DIR/portable-pg_ctl.err"
  fi
  wait_port "$PG_PORT" 30 || die "便携 PG 未在 30s 内监听 $PG_PORT，见 $PG_LOG / $TMP_DIR/portable-pg_ctl.err"
  # 端口 LISTEN != 可连接：PG 恢复阶段会拒绝普通连接，必须轮询到 SELECT 1 真正成功。
  _pg_ready=0
  _i=0
  while [ "$_i" -lt 60 ]; do
    _probe=$("$PSQL" -w -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d postgres -tAc 'SELECT 1' 2>/dev/null) || _probe=""
    if [ "$_probe" = "1" ]; then _pg_ready=1; break; fi
    sleep 0.5; _i=$((_i + 1))
  done
  [ "$_pg_ready" = "1" ] || die "便携 PG 未在 30s 内进入可连接状态，见 $PG_LOG"
  # 写入前确认连上的就是副本自己的 PG 实例：data_directory 必须等于副本 pgdata。
  _data_dir=$("$PSQL" -w -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d postgres -tAc 'SHOW data_directory' 2>/dev/null | tr -d '[:space:]')
  _expect_dir=$(readlink -f -- "$PGDATA"); _expect_dir=${_expect_dir%/}
  _dd_norm=$_data_dir; _dd_norm=${_dd_norm%/}
  [ "$_dd_norm" = "$_expect_dir" ] || die "便携 PG data_directory 不符（实际 '$_data_dir'，期望 '$_expect_dir'）：可能连到别的实例，拒绝写入"
  _db_exists=$("$PSQL" -w -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" 2>/dev/null) || _db_exists=""
  if [ -z "$_db_exists" ]; then "$PG_BIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" "$PG_DB" 2>&1 | sed 's/^/  /'; fi
  # P1-1：首次启动建 schema。alembic 迁移不带 schema 前缀，依赖 search_path 首位为 app；
  # 空库默认 search_path 只有 public，表会落错位置。两条语句均幂等，每次启动都跑，
  # 兼修复此前已建好的库（已有表不搬家，见 docs/workspace/zip-p1-1-clean-pg-record.md）。
  if ! _schema_out=$("$PSQL" -w -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -tAc 'CREATE SCHEMA IF NOT EXISTS app' 2>&1); then die "创建 app schema 失败：$_schema_out"; fi
  if ! _search_out=$("$PSQL" -w -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -tAc "ALTER DATABASE $PG_DB SET search_path TO app, public" 2>&1); then die "设置数据库 search_path 失败：$_search_out"; fi
  ok "便携 PG 就绪 127.0.0.1:$PG_PORT / $PG_DB"
  step '若数据库为空，请另行灌入快照（bundle_data.ps1 仅 Windows，Linux 侧待补）'
fi

# ------------------------------------------- 数据库迁移（alembic upgrade head，D1）
# 绿包首次启动时库是空的：此前没有任何脚本跑 alembic，后端起来后表不存在。
# upgrade head 幂等（已是最新时为空操作），每次启动都跑；失败直接退出，
# 不让后端带着未迁移的库启动。MY_APP_DB_MODE/DATABASE_URL 已由 set_cfg 导出进
# 进程环境，alembic env.py 直接读它们，与后端同一套连接串。
step '--- 数据库迁移（alembic upgrade head） ---'
if [ "$DRY_RUN" = "1" ]; then
  step '<干跑> 跳过 alembic upgrade head'
else
  if ! (cd "$MYAPP_DIR" && "$PY_EXE" -m alembic upgrade head >"$TMP_DIR/portable-alembic.out" 2>"$TMP_DIR/portable-alembic.err"); then
    die "alembic upgrade head 失败（见 $TMP_DIR/portable-alembic.err）：$(tail -n 10 "$TMP_DIR/portable-alembic.err" 2>/dev/null | tr '\n' ' ')"
  fi
  sed 's/^/  /' "$TMP_DIR/portable-alembic.out"
  ok 'alembic upgrade head 完成'
fi

# ------------------------------------------------- 2/6 nginx 配置（含 -t 校验）
step '--- 2/6 nginx 配置展开 ---'
START_NGINX=$(cfg_get 'START_NGINX' '1')
if [ "$START_NGINX" = "1" ] && [ "$SKIP_NGINX_CONF" != "1" ]; then
  if [ "$DRY_RUN" = "1" ]; then step '<干跑> 跳过 nginx 配置展开';
  else expand_nginx_conf; fi
fi

# ------------------------------------------------------------- 3/6 my_app 后端
step '--- 3/6 my_app 后端 ---'
start_detached 'backend' "$PY_EXE" "$MYAPP_DIR" "$BE_PORT" 45 -m uvicorn backend.main:app --host 127.0.0.1 --port "$BE_PORT" || die 'my_app 后端启动失败（见 TMP/portable-backend.err / .out）'

# ------------------------------------------------------------- 4/6 my_app 前端
step '--- 4/6 my_app 前端（vite preview） ---'
VITE_CLI="$FE_DIR/node_modules/vite/bin/vite.js"
start_detached 'frontend' "$NODE_EXE" "$FE_DIR" "$FE_PORT" 30 "$VITE_CLI" preview --host 127.0.0.1 --port "$FE_PORT" --strictPort || die 'my_app 前端启动失败（见 TMP/portable-frontend.err / .out）'

# ----------------------------------------------------------------- 5/6 门户
step '--- 5/6 统一门户 + 可选服务 ---'
if [ -n "$PORTAL_ROOT" ]; then
  TSX_CLI="$PORTAL_ROOT/node_modules/tsx/dist/cli.mjs"
  start_detached 'portal' "$NODE_EXE" "$PORTAL_ROOT" "$PORTAL_PORT" 30 "$TSX_CLI" server/index.ts || die '统一门户启动失败（见 TMP/portable-portal.err / .out）'
else
  warn '未找到门户目录（apps/portal），门户步骤跳过'
fi

if [ "$(cfg_get 'START_WA_ANALYSIS' '0')" = "1" ]; then
  WA_DIR="$ROOT/apps/portal/scripts/whatsapp_analysis"
  WA_CLI="$WA_DIR/node_modules/tsx/dist/cli.mjs"
  if [ -f "$WA_CLI" ]; then
    # 分析服务读 PORT；临时改成自己的端口，避免继承门户 PORT 而撞端口。
    _prev_port_set=0; _prev_port=""
    if [ -n "${PORT+x}" ]; then _prev_port_set=1; _prev_port=$PORT; fi
    export PORT="$WA_ANALYSIS_PORT"
    start_detached 'wa_analysis' "$NODE_EXE" "$WA_DIR" "$WA_ANALYSIS_PORT" 25 "$WA_CLI" server/index.ts
    _wa_ok=$?
    if [ "$_prev_port_set" = "1" ]; then export PORT="$_prev_port"; else unset PORT || true; fi
    [ "$_wa_ok" = "0" ] || die 'WhatsApp 分析服务启动失败（见 TMP/portable-wa_analysis.err）'
  else warn '分析服务依赖缺失，跳过'; fi
fi

if [ "$(cfg_get 'START_WA_BOT' '0')" = "1" ]; then
  warn 'START_WA_BOT=1：会使用副本里的 WhatsApp 登录态接管会话，务必确认已在目标机重新扫码'
  start_detached 'wa_bot' "$NODE_EXE" "$BOT_DIR" 0 0 --env-file-if-exists=.env src/index.js || true
fi

# 问答助手 9269 不在这里启动：由 my_app 后端 portal_qa_ctl.py 在首次提问时按需
# 拉起（PORTAL_QA_NODE 已指向副本自带 node），这里只需把环境变量备好。

# ----------------------------------------------------------------- 6/6 nginx
step '--- 6/6 nginx ---'
if [ "$START_NGINX" = "1" ]; then
  if own_port 'nginx' "$HTTP_PORT"; then :
  elif [ "$DRY_RUN" = "1" ]; then step '<干跑> 跳过 nginx 启动'
  elif [ ! -x "$NGINX_EXE" ]; then err "缺 $NGINX_EXE"
  else
    "$NGINX_EXE" -p "$NGINX_DIR/" -c "$NP_NGINX_CONF_OUT" >>"$TMP_DIR/portable-nginx.out" 2>>"$TMP_DIR/portable-nginx.err" || err "nginx 启动失败，见 $TMP_DIR/portable-nginx.err"
    NGINX_PID_FILE="$NP_NGINX_LOGS_DIR/nginx.pid"
    if wait_port "$HTTP_PORT" 15; then
      if [ -f "$NGINX_PID_FILE" ]; then
        _npid=$(tr -d '[:space:]' < "$NGINX_PID_FILE")
        case "$_npid" in ''|*[!0-9]*) ;; *) save_pid 'nginx' "$_npid" ;; esac
      fi
      ok "nginx 已监听 $HTTP_PORT"
    else err "nginx 未在 15s 内监听 $HTTP_PORT，见 $NP_NGINX_LOGS_DIR/error.log"; fi
  fi
else skip 'START_NGINX=0，不启动 nginx'; fi

# ------------------------------------------------------------------ 状态汇总
echo ''
echo '==================== 副本服务状态 ===================='
for _row in "nginx:$HTTP_PORT" "my_app 前端:$FE_PORT" "my_app 后端:$BE_PORT" "统一门户:$PORTAL_PORT" "便携 PG:$PG_PORT"; do
  _k=${_row%%:*}; _p=${_row#*:}
  if port_listening "$_p"; then _st='LISTENING'; else _st='not listening'; fi
  printf '  %-14s %-6s %s\n' "$_k" "$_p" "$_st"
done
echo ''
if [ "$HTTPS_PORT" = "443" ]; then HTTPS_BASE='https://localhost'; else HTTPS_BASE="https://localhost:$HTTPS_PORT"; fi
if [ "$HTTP_PORT" = "80" ]; then HTTP_BASE='http://localhost'; else HTTP_BASE="http://localhost:$HTTP_PORT"; fi
echo '  入口（HTTPS 自签证书，浏览器告警属正常；HTTP 同样可用）：'
echo "    $HTTPS_BASE/                 根导航"
echo "    $HTTPS_BASE/myapp/           Data Insight MVP"
echo "    $HTTPS_BASE/uniplam/         统一门户"
echo "    $HTTPS_BASE/v3/olbrportal/  问答助手挂件"
echo "    $HTTP_BASE/                 HTTP 备用"
echo ''
echo '  停止：./stop.sh（只停本副本进程）'
_elapsed=$(( $(date +%s) - START_S ))
if [ "$DRY_RUN" = "1" ]; then ok "start 完成，耗时 ${_elapsed} 秒（干跑）"; else ok "start 完成，耗时 ${_elapsed} 秒"; fi
