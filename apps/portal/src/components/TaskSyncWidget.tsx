import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  CheckCircle2,
  RotateCw,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  XCircle,
  WifiOff,
  Mail,
  Search,
  Ticket,
  RadioTower,
  Bot,
} from 'lucide-react';
import { getMyappTasksApi, getMyappLink } from '../config';
import { useI18n } from '../i18n';
import { formatMetric } from '../utils/format';

interface ApiTaskRun {
  success: boolean;
  started_at?: string;
  elapsed_seconds?: number;
}

interface ApiTask {
  task_id: string;
  name?: string;
  name_en?: string;
  name_zh?: string;
  category?: string;
  current_cron?: string;
  enabled?: boolean;
  running?: boolean;
  recent_runs?: ApiTaskRun[];
}

interface TaskItem {
  id: string;
  name: string;
  category: string;
  cron: string;
  enabled: boolean;
  running: boolean;
  lastSuccess: boolean | null;
  lastElapsed: number | null;
}

// 2026-11-23：原先这里有一份 35 条的 FALLBACK_TASKS「快照」兜底。
// 它让接口挂掉时页面仍旧显示「35 个任务 / 35 成功 / 0 失败」和精确到 0.1s 的耗时，
// 那些数字是某个历史时刻的静态值，不是当前状态 —— 看起来正常，实际在骗人。
// 现在改为：没有实时数据就显示不可用空态；本次会话拉取过数据后掉线，才显示那份数据并标注离线与时间。

const REFRESH_INTERVAL = 60_000;

function mapApiTask(t: ApiTask): TaskItem {
  const last = t.recent_runs && t.recent_runs.length > 0 ? t.recent_runs[0] : null;
  return {
    id: t.task_id,
    name: t.name_en || t.name || t.task_id,
    category: t.category || 'general',
    cron: t.current_cron || '',
    enabled: t.enabled !== false,
    running: !!t.running,
    lastSuccess: last ? !!last.success : null,
    lastElapsed: last && typeof last.elapsed_seconds === 'number' ? last.elapsed_seconds : null,
  };
}

export const TaskSyncWidget: React.FC = () => {
  const { t, lang } = useI18n();
  const [isExpanded, setIsExpanded] = useState(false);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [isLive, setIsLive] = useState(false);
  // 首次请求有结果前不判「不可用」，免得首屏闪一下错误态
  const [hasAttempted, setHasAttempted] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  // 本次会话真正拿到过接口数据；只有它为真才把 tasks 渲染成统计数字
  const hasData = tasks.length > 0;

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    const load = async () => {
      try {
        const res = await fetch(getMyappTasksApi(), {
          signal: controller.signal,
          headers: { 'Accept-Language': lang === 'zh' ? 'zh-CN' : 'en-US' },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        const list: ApiTask[] = Array.isArray(json?.tasks) ? json.tasks : [];
        if (!cancelled) {
          setHasAttempted(true);
          if (list.length > 0) {
            setTasks(list.map(mapApiTask));
            setIsLive(true);
            setUpdatedAt(new Date());
          } else {
            // 接口通了但一条任务都没有：同样是「没有可用数据」，不许沿用旧数组
            setIsLive(false);
          }
        }
      } catch {
        if (!cancelled) {
          setIsLive(false);
          setHasAttempted(true);
        }
      }
    };

    // 2026-10-01 修复 B6：原实现无脑每 60s 拉一次 /api/tasks，且响应体大（~77KB），
    // 就绪度检测实测单标签页 10 分钟 212 次请求 ≈ 100MB/小时。改为「仅在标签页可见时
    // 轮询」：隐藏时完全停，重新可见时立刻补一次拉取，避免后台标签页持续空耗带宽。
    load();
    let timer = window.setInterval(load, REFRESH_INTERVAL);
    const onVisibility = () => {
      if (document.hidden) {
        window.clearInterval(timer);
      } else {
        load();
        window.clearInterval(timer);
        timer = window.setInterval(load, REFRESH_INTERVAL);
      }
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [lang]);

  const stats = useMemo(() => {
    const total = tasks.length;
    const running = tasks.filter((t) => t.running).length;
    const failed = tasks.filter((t) => t.lastSuccess === false).length;
    const ok = tasks.filter((t) => t.lastSuccess === true).length;
    return { total, running, failed, ok };
  }, [tasks]);

  const showcase = useMemo(() => {
    const weight = (t: TaskItem) => (t.running ? 0 : t.lastSuccess === false ? 1 : 2);
    return [...tasks].sort((a, b) => weight(a) - weight(b)).slice(0, 8);
  }, [tasks]);

  return (
    <div className="spatial-panel mb-4 sm:mb-6 bg-gradient-to-r from-teal-50 via-white to-cyan-50 text-slate-900 rounded-2xl p-3 sm:p-5 border border-teal-300/50 shadow-sm dark:bg-gradient-to-r dark:from-navy-950 dark:via-navy-900 dark:to-navy-800 dark:text-white dark:border-teal-400/15 dark:shadow-lg dark:shadow-navy-950/40 animate-fadeIn">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-2 sm:gap-3 min-w-0">
          <div className="p-2 sm:p-2.5 rounded-xl bg-teal-100 text-teal-600 border border-teal-300/70 dark:bg-teal-400/15 dark:text-teal-300 dark:border-teal-400/30 shrink-0">
            <Activity className="w-4 h-4 sm:w-5 sm:h-5 animate-pulse" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="font-bold text-sm sm:text-base tracking-tight text-slate-900 dark:text-white flex items-center gap-2">
                {t('task.title')}
              </h3>
              {isLive ? (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-700 border border-emerald-300/70 dark:bg-emerald-500/20 dark:text-emerald-300 dark:border-emerald-400/30 flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 dark:bg-emerald-400 animate-pulse" />
                  {t('task.live')}
                  {updatedAt && (
                    <span className="text-emerald-600/70 dark:text-emerald-400/70 font-mono hidden sm:inline">
                      {updatedAt.toLocaleTimeString(lang === 'zh' ? 'zh-CN' : 'en-US', { hour12: false })}
                    </span>
                  )}
                </span>
              ) : hasData ? (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-100 text-amber-700 border border-amber-300/70 dark:bg-amber-500/20 dark:text-amber-300 dark:border-amber-400/30 flex items-center gap-1">
                  <WifiOff className="w-2.5 h-2.5" />
                  {t('task.offline')}
                  {updatedAt && (
                    <span className="text-amber-700/70 dark:text-amber-300/70 font-mono hidden sm:inline">
                      {updatedAt.toLocaleTimeString(lang === 'zh' ? 'zh-CN' : 'en-US', { hour12: false })}
                    </span>
                  )}
                </span>
              ) : hasAttempted ? (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-100 text-rose-700 border border-rose-300/70 dark:bg-rose-500/20 dark:text-rose-300 dark:border-rose-400/30 flex items-center gap-1">
                  <WifiOff className="w-2.5 h-2.5" />
                  {t('task.unavailable')}
                </span>
              ) : (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-100 text-slate-600 border border-slate-300/70 dark:bg-white/10 dark:text-slate-300 dark:border-white/15 flex items-center gap-1">
                  <RotateCw className="w-2.5 h-2.5 animate-spin" />
                  {t('task.loading')}
                </span>
              )}
            </div>
            <p className="text-[11px] sm:text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              {t('task.desc')}
            </p>
          </div>
        </div>

        {/* Stats Pills + Actions */}
        <div className="flex flex-wrap items-center gap-1 sm:gap-2">
          {hasData && (
            <div className="flex items-center gap-1 sm:gap-1.5 bg-slate-900/5 dark:bg-white/5 px-1.5 sm:px-3 py-1 sm:py-1.5 rounded-xl border border-slate-300/60 dark:border-white/10 text-xs flex-wrap whitespace-nowrap">
              <span className="text-slate-500 dark:text-slate-400">{t('task.total')}</span>
              <span className="font-mono font-bold text-slate-900 dark:text-white">{stats.total}</span>
              <span className="text-slate-300 dark:text-slate-600">|</span>
              <span className="text-teal-600 dark:text-teal-300 flex items-center gap-1">
                <RotateCw className={`w-3 h-3 ${stats.running > 0 ? 'animate-spin' : ''}`} /> {stats.running} {t('task.running')}
              </span>
              <span className="text-slate-300 dark:text-slate-600">|</span>
              <span className="text-emerald-600 dark:text-emerald-400">{stats.ok} {t('task.ok')}</span>
              <span className="text-slate-300 dark:text-slate-600">|</span>
              <span className={stats.failed > 0 ? 'text-rose-500 dark:text-rose-400 font-bold' : 'text-slate-400 dark:text-slate-500'}>
                {stats.failed} {t('task.failed')}
              </span>
            </div>
          )}

          <div className="hidden xl:flex items-center gap-1.5 pl-1 border-l border-slate-200 dark:border-white/10">
            {/* 2026-10-24：经 .20 的 9443 反代到 .27 的 Open WebUI。
                原因：.27:8443 不在 VPN/防火墙放行名单，登录 VPN 后超时；而 .20 在
                名单内且与 .27 同网段直连可达。反代配置见 nginx/conf/portal_ports.conf
                末尾的 9443 server 块（与 8081-8088 同构）。 */}
            <a href="https://192.168.100.20:9443/" target="_blank" rel="noopener noreferrer" title={t('task.ragKnowledgeQa')}
              className="px-2.5 py-1.5 rounded-xl bg-violet-50 hover:bg-violet-100 text-violet-700 dark:bg-violet-950/50 dark:hover:bg-violet-900/60 dark:text-violet-300 text-xs font-medium flex items-center gap-1 transition-colors">
              <Bot className="w-3.5 h-3.5" />{t('task.ragKnowledgeQa')}
            </a>
            <a href={getMyappLink('eflow_qa')} target="_blank" rel="noopener noreferrer" title={t('task.knowledgeSearch')}
              className="px-2.5 py-1.5 rounded-xl bg-cyan-50 hover:bg-cyan-100 text-cyan-700 dark:bg-cyan-950/50 dark:hover:bg-cyan-900/60 dark:text-cyan-300 text-xs font-medium flex items-center gap-1 transition-colors">
              <Search className="w-3.5 h-3.5" />{t('task.knowledgeSearch')}
            </a>
            <a href={getMyappLink('eflow_qa', 'tt-quick-analysis')} target="_blank" rel="noopener noreferrer" title={t('task.ttQuickAnalysis')}
              className="px-2.5 py-1.5 rounded-xl bg-amber-50 hover:bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:hover:bg-amber-900/60 dark:text-amber-300 text-xs font-medium flex items-center gap-1 transition-colors">
              <Ticket className="w-3.5 h-3.5" />{t('task.ttQuickAnalysis')}
            </a>
            <a href={getMyappLink('eflow_qa', 'ran-ume-alarm-analysis')} target="_blank" rel="noopener noreferrer" title={t('task.ranUmeAlarmAnalysis')}
              className="px-2.5 py-1.5 rounded-xl bg-rose-50 hover:bg-rose-100 text-rose-700 dark:bg-rose-950/50 dark:hover:bg-rose-900/60 dark:text-rose-300 text-xs font-medium flex items-center gap-1 transition-colors">
              <RadioTower className="w-3.5 h-3.5" />{t('task.ranUmeAlarmAnalysis')}
            </a>
          </div>

          <a
            href={getMyappLink('email_reports')}
            target="_blank"
            rel="noopener noreferrer"
            className="px-1.5 sm:px-3 py-1 sm:py-1.5 rounded-xl bg-gradient-to-r from-teal-600 to-cyan-600 hover:from-teal-500 hover:to-cyan-500 text-white font-medium text-xs flex items-center gap-1 transition-all shadow-sm shadow-teal-600/30"
          >
            <Mail className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">{t('task.latestReports')}</span>
          </a>

          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="p-2 sm:p-1.5 rounded-xl bg-slate-900/5 hover:bg-slate-900/10 text-slate-600 border border-slate-300/60 dark:bg-white/5 dark:hover:bg-white/10 dark:text-slate-300 dark:border-white/10 transition-colors"
            title={isExpanded ? t('task.collapse') : t('task.expand')}
          >
            {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>

          <a
            href={getMyappLink('sync')}
            target="_blank"
            rel="noopener noreferrer"
            className="px-2 sm:px-3 py-1 sm:py-1.5 rounded-xl bg-gradient-to-r from-teal-600 to-cyan-600 hover:from-teal-500 hover:to-cyan-500 text-white font-medium text-xs flex items-center gap-1 transition-all shadow-sm shadow-teal-600/30"
          >
            <span className="hidden sm:inline">{t('task.bigScreen')}</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        </div>
      </div>

      {/* Expandable Task Cards：没有实时数据时不渲染任何卡片，改为明确的不可用说明 */}
      {isExpanded && hasData && (
        <div className="mt-3 sm:mt-4 pt-3 sm:pt-4 border-t border-slate-200 dark:border-white/10 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 sm:gap-3 animate-fadeIn">
          {showcase.map((task) => (
            <div
              key={task.id}
              className="p-2.5 sm:p-3 rounded-xl bg-white/70 border border-slate-200 hover:border-teal-500/50 dark:bg-white/5 dark:border-white/10 dark:hover:border-teal-400/40 flex flex-col justify-between transition-colors"
            >
              <div>
                <div className="flex items-center justify-between mb-1 sm:mb-1.5">
                  <span className="text-[10px] font-semibold px-1.5 sm:px-2 py-0.5 rounded-md bg-teal-100 text-teal-700 border border-teal-300/70 dark:bg-teal-950/80 dark:text-teal-300 dark:border-teal-400/20">
                    {task.category}
                  </span>
                  {task.running ? (
                    <span className="text-[10px] text-teal-600 dark:text-teal-300 font-medium flex items-center gap-1">
                      <RotateCw className="w-2.5 h-2.5 animate-spin" /> {t('task.taskRunning')}
                    </span>
                  ) : task.lastSuccess === false ? (
                    <span className="text-[10px] text-rose-500 dark:text-rose-400 font-medium flex items-center gap-1">
                      <XCircle className="w-2.5 h-2.5" /> {t('task.lastFailed')}
                    </span>
                  ) : (
                    <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-medium flex items-center gap-1">
                      <CheckCircle2 className="w-2.5 h-2.5" /> {t('task.ready')}
                    </span>
                  )}
                </div>

                <h4 className="font-semibold text-xs text-slate-900 dark:text-white truncate" title={task.name}>
                  {task.name}
                </h4>
                <p className="text-[10px] sm:text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 sm:mt-1 leading-snug font-mono">{task.id}</p>
              </div>

              <div className="mt-2 sm:mt-2.5 pt-1.5 sm:pt-2 border-t border-slate-200/80 dark:border-white/10 text-[10px] text-slate-500 dark:text-slate-400 space-y-0.5">
                <div className="flex items-center justify-between">
                  <span>{t('task.cron')}</span>
                  <span className="font-mono text-slate-700 dark:text-slate-300">{task.cron}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span>{t('task.elapsed')}</span>
                  <span className="font-mono text-slate-700 dark:text-slate-300">
                    {formatMetric(task.lastElapsed, { digits: 1, suffix: 's' })}
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {isExpanded && !hasData && (
        <div className="mt-3 sm:mt-4 pt-3 sm:pt-4 border-t border-slate-200 dark:border-white/10">
          <div className="flex flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-slate-300 bg-white/60 px-4 py-5 text-center dark:border-white/15 dark:bg-white/5">
            <WifiOff className="h-4 w-4 text-slate-400 dark:text-slate-500" />
            <div className="text-xs font-semibold text-slate-700 dark:text-slate-200">{t('task.unavailable')}</div>
            <p className="max-w-md text-[11px] leading-snug text-slate-500 dark:text-slate-400">
              {hasAttempted ? t('task.unavailableHint') : t('task.loading')}
            </p>
          </div>
        </div>
      )}
    </div>
  );
};
