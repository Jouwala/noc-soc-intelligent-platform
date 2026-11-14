import React from 'react';
import { ListTodo, ScrollText, RefreshCw } from 'lucide-react';
import { useI18n } from '../../i18n';
import {
  useAgentEvents,
  useNow,
  formatRelativeTime,
  type AgentTaskState,
  type UseAgentEventsResult,
} from './hooks/useAgentEvents';

/* AgentTasksView —— 控制台「任务 + 执行日志流」视图（TASK-08 外壳）
 * 任务行：Issue 编号/标题、协议状态徽标、认领关系（assignee）、进度、
 * 分支、更新时间；下方是执行日志流（协议事件，SSE 实时追加，新在前）。
 * 进度缺失显示 —（不显示 0，门户口径：没取到 ≠ 真的是 0）。 */

export const TASK_STATE_META: Record<AgentTaskState, { key: string; cls: string }> = {
  todo: {
    key: 'agent.stateTodo',
    cls: 'bg-slate-50 dark:bg-slate-800 text-slate-600 dark:text-slate-300 border-slate-200 dark:border-slate-700',
  },
  claimed: {
    key: 'agent.stateClaimed',
    cls: 'bg-sky-50 dark:bg-sky-950/40 text-sky-700 dark:text-sky-300 border-sky-200 dark:border-sky-900',
  },
  'in-review': {
    key: 'agent.stateInReview',
    cls: 'bg-violet-50 dark:bg-violet-950/40 text-violet-700 dark:text-violet-300 border-violet-200 dark:border-violet-900',
  },
  'qa-ready': {
    key: 'agent.stateQaReady',
    cls: 'bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-900',
  },
  'qa-passed': {
    key: 'agent.stateQaPassed',
    cls: 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900',
  },
  'qa-failed': {
    key: 'agent.stateQaFailed',
    cls: 'bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-900',
  },
  merged: {
    key: 'agent.stateMerged',
    cls: 'bg-teal-50 dark:bg-teal-950/40 text-teal-700 dark:text-teal-300 border-teal-200 dark:border-teal-900',
  },
  blocked: {
    key: 'agent.stateBlocked',
    cls: 'bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-900',
  },
};

export default function AgentTasksView({ data }: { data?: UseAgentEventsResult }) {
  const { t } = useI18n();
  const own = useAgentEvents({ enabled: !data });
  const d = data ?? own;
  const now = useNow();

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="flex items-center gap-2 text-sm font-bold text-slate-700 dark:text-slate-200">
            <ListTodo className="w-4 h-4" />
            {t('agent.tasks')}（{d.tasks.length}）
          </h3>
          <button
            onClick={d.refresh}
            className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium rounded-lg bg-slate-50 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
          >
            <RefreshCw className="w-3 h-3" /> {t('agent.refresh')}
          </button>
        </div>

        {d.connection === 'offline' && d.tasks.length === 0 && (
          <div className="rounded-2xl border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/40 p-4 text-sm">
            <div className="font-semibold text-amber-800 dark:text-amber-200">{t('agent.backendOffline')}</div>
            <div className="text-xs text-amber-700 dark:text-amber-300 mt-1">{t('agent.backendOfflineHint')}</div>
          </div>
        )}

        {!d.loading && d.connection !== 'offline' && d.tasks.length === 0 && (
          <div className="spatial-panel rounded-3xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-10 text-center text-sm text-slate-500 dark:text-slate-400">
            {t('agent.emptyTasks')}
          </div>
        )}

        {d.tasks.length > 0 && (
          <div className="spatial-panel rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 divide-y divide-slate-100 dark:divide-slate-800">
            {d.tasks.map((task) => {
              const meta = TASK_STATE_META[task.state];
              return (
                <div key={task.id} className="p-3.5 flex flex-col gap-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-slate-800 dark:text-slate-100 truncate">
                      #{task.id} {task.title}
                    </span>
                    <span
                      className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full border font-semibold ${meta.cls}`}
                    >
                      {t(meta.key)}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
                    <span>
                      {t('agent.assignee')}: {task.assignee ?? '—'}
                    </span>
                    {task.branch && (
                      <span className="break-all">
                        {t('agent.branch')}: {task.branch}
                      </span>
                    )}
                    {task.prNumber != null && <span>PR #{task.prNumber}</span>}
                    <span>
                      {t('agent.updatedAt')}: {formatRelativeTime(task.updatedAt, now, t)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                    <span>{t('agent.progress')}:</span>
                    {task.progress != null ? (
                      <>
                        <span className="flex-1 max-w-48 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
                          <span
                            className="block h-full rounded-full bg-teal-500"
                            style={{ width: `${Math.min(100, Math.max(0, task.progress))}%` }}
                          />
                        </span>
                        <span>{task.progress}%</span>
                      </>
                    ) : (
                      <span>—</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h3 className="flex items-center gap-2 text-sm font-bold text-slate-700 dark:text-slate-200">
          <ScrollText className="w-4 h-4" />
          {t('agent.logStream')}（{d.events.length}）
        </h3>
        {d.events.length === 0 ? (
          <div className="spatial-panel rounded-3xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 text-center text-sm text-slate-500 dark:text-slate-400">
            {t('agent.emptyLog')}
          </div>
        ) : (
          <div className="spatial-panel rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-3 max-h-96 overflow-auto space-y-1.5">
            {d.events.map((ev) => (
              <div key={ev.id} className="flex items-baseline gap-2 text-xs text-slate-600 dark:text-slate-300">
                <span className="shrink-0 text-slate-400 tabular-nums">
                  {new Date(ev.at).toLocaleTimeString()}
                </span>
                <span className="shrink-0 px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-[10px] font-semibold text-slate-600 dark:text-slate-300">
                  {ev.type}
                </span>
                {ev.agentId && <span className="shrink-0 text-slate-400">{ev.agentId}</span>}
                {ev.taskId != null && <span className="shrink-0 text-slate-400">#{ev.taskId}</span>}
                <span className="break-all">{ev.message}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
