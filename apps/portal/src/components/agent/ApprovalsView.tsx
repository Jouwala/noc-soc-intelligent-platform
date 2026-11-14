import React, { useState } from 'react';
import { ShieldCheck, AlertTriangle, Check, X as XIcon } from 'lucide-react';
import { useI18n } from '../../i18n';
import {
  useAgentEvents,
  useNow,
  formatRelativeTime,
  type ApprovalDecision,
  type UseAgentEventsResult,
} from './hooks/useAgentEvents';
import { TASK_STATE_META } from './AgentTasksView';

/* ApprovalsView —— 控制台「异常 + 审批队列」视图（TASK-08 外壳）
 * 异常 = 状态为 blocked / qa-failed 的任务（常驻可见，不收起 —— 门户口径：
 * 「这次算不全/出问题了」必须摆在明处）。审批队列：待批项可直接批准/驳回，
 * 决定经 useAgentEvents.decideApproval 提交服务端并记事件（本地日志流与
 * 服务端 SSE 都会出现 APPROVAL_DECIDED）；提交失败保留待批项并显示错误。 */

export default function ApprovalsView({ data }: { data?: UseAgentEventsResult }) {
  const { t } = useI18n();
  const own = useAgentEvents({ enabled: !data });
  const d = data ?? own;
  const now = useNow();
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [decideError, setDecideError] = useState<string | null>(null);

  const exceptions = d.tasks.filter((task) => task.state === 'blocked' || task.state === 'qa-failed');
  const pending = d.approvals.filter((a) => a.status === 'pending');
  const decided = d.approvals.filter((a) => a.status !== 'pending');

  async function decide(id: string, decision: ApprovalDecision) {
    setDecidingId(id);
    setDecideError(null);
    try {
      await d.decideApproval(id, decision);
    } catch {
      setDecideError(t('agent.decideFailed'));
    } finally {
      setDecidingId(null);
    }
  }

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <h3 className="flex items-center gap-2 text-sm font-bold text-slate-700 dark:text-slate-200">
          <AlertTriangle className="w-4 h-4" />
          {t('agent.exceptions')}（{exceptions.length}）
        </h3>
        {exceptions.length === 0 ? (
          <div className="spatial-panel rounded-3xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 text-center text-sm text-slate-500 dark:text-slate-400">
            {t('agent.emptyExceptions')}
          </div>
        ) : (
          <div className="spatial-panel rounded-2xl border border-rose-200 dark:border-rose-900 bg-white dark:bg-slate-900 divide-y divide-slate-100 dark:divide-slate-800">
            {exceptions.map((task) => {
              const meta = TASK_STATE_META[task.state];
              return (
                <div key={task.id} className="p-3.5 flex items-center justify-between gap-2">
                  <span className="text-sm text-slate-800 dark:text-slate-100 truncate">
                    #{task.id} {task.title}
                    <span className="text-xs text-slate-400 ml-2">
                      {t('agent.assignee')}: {task.assignee ?? '—'} · {t('agent.updatedAt')}:{' '}
                      {formatRelativeTime(task.updatedAt, now, t)}
                    </span>
                  </span>
                  <span
                    className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full border font-semibold ${meta.cls}`}
                  >
                    {t(meta.key)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h3 className="flex items-center gap-2 text-sm font-bold text-slate-700 dark:text-slate-200">
          <ShieldCheck className="w-4 h-4" />
          {t('agent.approvals')}（{pending.length}）
        </h3>

        {decideError && (
          <div className="rounded-2xl border border-rose-200 dark:border-rose-900 bg-rose-50 dark:bg-rose-950/40 p-3 text-xs text-rose-700 dark:text-rose-300">
            {decideError}
          </div>
        )}

        {pending.length === 0 ? (
          <div className="spatial-panel rounded-3xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 text-center text-sm text-slate-500 dark:text-slate-400">
            {t('agent.emptyApprovals')}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {pending.map((item) => (
              <div
                key={item.id}
                className="rounded-2xl border p-4 bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800"
              >
                <div className="flex items-center justify-between gap-2 mb-1.5">
                  <span className="text-sm font-bold text-slate-800 dark:text-slate-100">{item.title}</span>
                  <span className="shrink-0 text-[10px] px-2 py-0.5 rounded-full border font-semibold bg-slate-50 dark:bg-slate-800 text-slate-600 dark:text-slate-300 border-slate-200 dark:border-slate-700">
                    {item.kind}
                  </span>
                </div>
                {item.summary && (
                  <p className="text-xs text-slate-600 dark:text-slate-300 mb-2 break-all">{item.summary}</p>
                )}
                <div className="text-xs text-slate-400 mb-3">
                  {t('agent.requestedBy')}: {item.requestedBy}
                  {item.taskId != null && <> · #{item.taskId}</>} ·{' '}
                  {formatRelativeTime(item.createdAt, now, t)}
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => void decide(item.id, 'approved')}
                    disabled={decidingId === item.id}
                    className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium rounded-lg bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-100 dark:hover:bg-emerald-900/40 transition-colors disabled:opacity-50"
                  >
                    <Check className="w-3 h-3" /> {t('agent.approve')}
                  </button>
                  <button
                    onClick={() => void decide(item.id, 'rejected')}
                    disabled={decidingId === item.id}
                    className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium rounded-lg bg-slate-50 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors disabled:opacity-50"
                  >
                    <XIcon className="w-3 h-3" /> {t('agent.reject')}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {decided.length > 0 && (
          <div className="spatial-panel rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 divide-y divide-slate-100 dark:divide-slate-800">
            {decided.map((item) => (
              <div key={item.id} className="p-3 flex items-center justify-between gap-2 opacity-70">
                <span className="text-xs text-slate-600 dark:text-slate-300 truncate">
                  {item.title}
                  <span className="text-slate-400 ml-2">{formatRelativeTime(item.decidedAt, now, t)}</span>
                </span>
                <span
                  className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full border font-semibold ${
                    item.status === 'approved'
                      ? 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900'
                      : 'bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-900'
                  }`}
                >
                  {t(item.status === 'approved' ? 'agent.approved' : 'agent.rejected')}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
