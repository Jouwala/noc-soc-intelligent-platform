import React from 'react';
import { Server, RefreshCw } from 'lucide-react';
import { useI18n } from '../../i18n';
import {
  useAgentEvents,
  useNow,
  deriveNodeStatus,
  formatRelativeTime,
  type AgentNodeStatus,
  type UseAgentEventsResult,
} from './hooks/useAgentEvents';

/* AgentNodesView —— 控制台「节点」视图（TASK-08 外壳）
 * 节点 = 注册表里的各 Agent（Muse 1/2/3、Grok 等）：状态（在线/失联/离线）
 * 由服务端状态或心跳新鲜度推导（deriveNodeStatus），数据经 useAgentEvents
 * 的快照 + SSE 增量实时更新，不轮询。后端不可达时只显示离线提示与空态，
 * 不造假数据。 */

const STATUS_META: Record<AgentNodeStatus, { key: string; dot: string; cls: string }> = {
  online: {
    key: 'agent.online',
    dot: 'bg-emerald-500',
    cls: 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900',
  },
  stale: {
    key: 'agent.stale',
    dot: 'bg-amber-500',
    cls: 'bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-900',
  },
  offline: {
    key: 'agent.offline',
    dot: 'bg-rose-500',
    cls: 'bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-900',
  },
};

const CONNECTION_META: Record<UseAgentEventsResult['connection'], { key: string; cls: string }> = {
  live: {
    key: 'agent.live',
    cls: 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900',
  },
  connecting: {
    key: 'agent.connecting',
    cls: 'bg-slate-50 dark:bg-slate-800 text-slate-500 dark:text-slate-400 border-slate-200 dark:border-slate-700',
  },
  reconnecting: {
    key: 'agent.reconnecting',
    cls: 'bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-900',
  },
  offline: {
    key: 'agent.offline',
    cls: 'bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-900',
  },
};

export default function AgentNodesView({ data }: { data?: UseAgentEventsResult }) {
  const { t } = useI18n();
  const own = useAgentEvents({ enabled: !data });
  const d = data ?? own;
  const now = useNow();
  const conn = CONNECTION_META[d.connection];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-bold text-slate-700 dark:text-slate-200">
          <Server className="w-4 h-4" />
          {t('agent.nodes')}（{d.nodes.length}）
        </h3>
        <div className="flex items-center gap-2">
          <span className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold ${conn.cls}`}>
            {t(conn.key)}
          </span>
          <button
            onClick={d.refresh}
            className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium rounded-lg bg-slate-50 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
          >
            <RefreshCw className="w-3 h-3" /> {t('agent.refresh')}
          </button>
        </div>
      </div>

      {d.connection === 'offline' && d.nodes.length === 0 && (
        <div className="rounded-2xl border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/40 p-4 text-sm">
          <div className="font-semibold text-amber-800 dark:text-amber-200">{t('agent.backendOffline')}</div>
          <div className="text-xs text-amber-700 dark:text-amber-300 mt-1">{t('agent.backendOfflineHint')}</div>
        </div>
      )}

      {!d.loading && d.connection !== 'offline' && d.nodes.length === 0 && (
        <div className="spatial-panel rounded-3xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-10 text-center text-sm text-slate-500 dark:text-slate-400">
          {t('agent.emptyNodes')}
        </div>
      )}

      {d.nodes.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          {d.nodes.map((node) => {
            const status = deriveNodeStatus(node, now);
            const meta = STATUS_META[status];
            return (
              <div
                key={node.id}
                className="rounded-2xl border p-4 bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800"
              >
                <div className="flex items-center justify-between gap-2 mb-2">
                  <span className="text-sm font-bold text-slate-800 dark:text-slate-100">{node.name}</span>
                  <span
                    className={`flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full border font-semibold ${meta.cls}`}
                  >
                    <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} />
                    {t(meta.key)}
                  </span>
                </div>
                <div className="text-xs text-slate-500 dark:text-slate-400 mb-3">{node.role}</div>
                <dl className="text-xs space-y-1 text-slate-600 dark:text-slate-300">
                  <div>
                    <dt className="inline text-slate-400">{t('agent.lastHeartbeat')}: </dt>
                    <dd className="inline">{formatRelativeTime(node.lastHeartbeatAt, now, t)}</dd>
                  </div>
                  <div>
                    <dt className="inline text-slate-400">{t('agent.currentTask')}: </dt>
                    <dd className="inline">
                      {node.currentTaskId != null ? `#${node.currentTaskId}` : t('agent.none')}
                    </dd>
                  </div>
                </dl>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
