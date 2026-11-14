import { useCallback, useEffect, useRef, useState } from 'react';
import { PORTAL_API } from '../../../config';

/* ============================================================================
 * useAgentEvents.ts —— 统一 Agent 管理控制台（TASK-08）的数据层（外壳先行）
 * ============================================================================
 *
 * 数据契约（后端 = TASK-02 的 GitHub 原生聚合接口，控制面提供）：
 *   GET  {BASE}/agent-console/snapshot   → AgentConsoleSnapshot（全量）
 *   GET  {BASE}/agent-console/events     → SSE 增量流（见下方事件名）
 *   POST {BASE}/agent-console/approvals/{id}/decision  { decision }
 * BASE 默认走门户的 PORTAL_API（src/config.ts，带 /uniplam 前缀 —— 门户
 * AGENTS.md 硬性约束：裸 /api/ 会被 nginx 打到 my_app 后端）；本地联调可用
 * VITE_AGENT_CONSOLE_API_BASE 覆盖。
 *
 * SSE 事件名与载荷：
 *   snapshot → AgentConsoleSnapshot（连接建立/重连后服务端先推一份全量）
 *   node     → AgentNode（按 id upsert）
 *   task     → AgentTask（按 id upsert）
 *   approval → ApprovalItem（按 id upsert）
 *   log      → AgentEvent（追加到日志流，本地保留最近 200 条）
 *
 * 为什么不用 EventSource 而是 fetch + ReadableStream 自己解析：
 * 与 apps/my_app/frontend/src/lib/sseStream.ts 的理由一致——EventSource 带不上
 * 自定义头、拿不到 HTTP 状态码。解析器（createSseParser）的语义与 sseStream.ts
 * 保持一致（帧切分、\r\n 归一、多行 data 拼接、注释行忽略、非 JSON data 原样
 * 返回）；门户与 my_app 是两个独立 Vite 应用、不能跨应用 import，故在此内嵌
 * 同语义实现，不要在视图里再写一份解析。
 *
 * 后端未部署/不可达时：connection 置 offline，视图显示空态与离线提示，
 * **不展示任何假数据**（外壳的职责是把真实状态摆出来）。
 * ========================================================================== */

const API_BASE =
  (import.meta.env.VITE_AGENT_CONSOLE_API_BASE as string | undefined)?.replace(/\/+$/, '') ||
  PORTAL_API;

export type AgentNodeStatus = 'online' | 'stale' | 'offline';

export interface AgentNode {
  id: string;
  name: string;
  role: string;
  /** 服务端可直接给状态；缺省时由 lastHeartbeatAt 推导（见 deriveNodeStatus） */
  status?: AgentNodeStatus;
  lastHeartbeatAt: string | null;
  currentTaskId: number | null;
}

export type AgentTaskState =
  | 'todo'
  | 'claimed'
  | 'in-review'
  | 'qa-ready'
  | 'qa-passed'
  | 'qa-failed'
  | 'merged'
  | 'blocked';

export interface AgentTask {
  /** Issue 编号 */
  id: number;
  title: string;
  state: AgentTaskState;
  /** 认领关系：agent id（如 muse-2），未认领为 null */
  assignee: string | null;
  /** 0-100；无进度信号时为 null */
  progress: number | null;
  branch: string | null;
  prNumber: number | null;
  url: string;
  updatedAt: string;
}

export interface AgentEvent {
  id: string;
  at: string;
  agentId: string | null;
  /** 协议 12 类消息名（TASK_ACCEPTED / TASK_PROGRESS / …）或 APPROVAL_DECIDED */
  type: string;
  taskId: number | null;
  message: string;
}

export type ApprovalStatus = 'pending' | 'approved' | 'rejected';
export type ApprovalDecision = 'approved' | 'rejected';

export interface ApprovalItem {
  id: string;
  kind: string;
  title: string;
  summary: string;
  requestedBy: string;
  taskId: number | null;
  createdAt: string;
  status: ApprovalStatus;
  decidedBy: string | null;
  decidedAt: string | null;
}

export interface AgentConsoleSnapshot {
  nodes: AgentNode[];
  tasks: AgentTask[];
  approvals: ApprovalItem[];
  events: AgentEvent[];
}

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'offline';

export interface UseAgentEventsResult {
  nodes: AgentNode[];
  tasks: AgentTask[];
  events: AgentEvent[];
  approvals: ApprovalItem[];
  connection: ConnectionState;
  loading: boolean;
  refresh: () => void;
  decideApproval: (id: string, decision: ApprovalDecision) => Promise<void>;
}

/* ───────────────────────── SSE 增量解析器（语义同 sseStream.ts） ───────────────────────── */

export interface SseEvent {
  event: string;
  data: unknown;
}

export function createSseParser() {
  let buffer = '';

  function parseBlock(block: string): SseEvent | null {
    let event = 'message';
    const dataLines: string[] = [];
    for (const rawLine of block.split('\n')) {
      const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
      if (!line || line.startsWith(':')) continue;
      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? '' : line.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'event') event = value;
      else if (field === 'data') dataLines.push(value);
    }
    if (!dataLines.length) return null;
    const text = dataLines.join('\n');
    let data: unknown = text;
    try {
      data = JSON.parse(text);
    } catch {
      /* 非 JSON 就原样给字符串 */
    }
    return { event, data };
  }

  return {
    feed(chunk: string): SseEvent[] {
      buffer += chunk;
      if (buffer.includes('\r')) buffer = buffer.replace(/\r\n/g, '\n');
      const out: SseEvent[] = [];
      for (;;) {
        const idx = buffer.indexOf('\n\n');
        if (idx < 0) break;
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const ev = parseBlock(block);
        if (ev) out.push(ev);
      }
      return out;
    },
    flush(): SseEvent[] {
      const rest = buffer;
      buffer = '';
      if (!rest.trim()) return [];
      const ev = parseBlock(rest);
      return ev ? [ev] : [];
    },
  };
}

/* ───────────────────────── 工具 ───────────────────────── */

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null;
}

function upsertById<T extends { id: string | number }>(list: T[], item: T): T[] {
  const idx = list.findIndex((x) => x.id === item.id);
  if (idx < 0) return [item, ...list];
  const next = list.slice();
  next[idx] = item;
  return next;
}

/** 节点状态推导：服务端没给 status 时按心跳新鲜度判（≤5min 在线、≤30min 失联中、其余离线） */
export function deriveNodeStatus(node: AgentNode, nowMs: number): AgentNodeStatus {
  if (node.status) return node.status;
  if (!node.lastHeartbeatAt) return 'offline';
  const age = nowMs - Date.parse(node.lastHeartbeatAt);
  if (Number.isNaN(age)) return 'offline';
  if (age <= 5 * 60_000) return 'online';
  if (age <= 30 * 60_000) return 'stale';
  return 'offline';
}

type TranslateFn = (key: string, params?: Record<string, string | number>) => string;

/** 相对时间（视图共用；文案走调用方传入的 t） */
export function formatRelativeTime(iso: string | null, nowMs: number, t: TranslateFn): string {
  if (!iso) return '—';
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '—';
  const diff = Math.max(0, nowMs - then);
  if (diff < 60_000) return t('agent.justNow');
  if (diff < 3_600_000) return t('agent.minutesAgo', { n: Math.floor(diff / 60_000) });
  if (diff < 86_400_000) return t('agent.hoursAgo', { n: Math.floor(diff / 3_600_000) });
  return t('agent.daysAgo', { n: Math.floor(diff / 86_400_000) });
}

/** 每 intervalMs 刷新一次的当前时间（相对时间显示用） */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

const EMPTY_SNAPSHOT: AgentConsoleSnapshot = { nodes: [], tasks: [], approvals: [], events: [] };
const MAX_LOG_EVENTS = 200;

/* ───────────────────────── Hook ───────────────────────── */

export function useAgentEvents(options?: { enabled?: boolean }): UseAgentEventsResult {
  const enabled = options?.enabled !== false;
  const [snapshot, setSnapshot] = useState<AgentConsoleSnapshot>(EMPTY_SNAPSHOT);
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [loading, setLoading] = useState<boolean>(enabled);
  const [refreshTick, setRefreshTick] = useState(0);
  const everConnectedRef = useRef(false);

  const applyEvent = useCallback((ev: SseEvent) => {
    const data = ev.data;
    switch (ev.event) {
      case 'snapshot':
        if (isRecord(data)) {
          setSnapshot({
            nodes: Array.isArray(data.nodes) ? (data.nodes as AgentNode[]) : [],
            tasks: Array.isArray(data.tasks) ? (data.tasks as AgentTask[]) : [],
            approvals: Array.isArray(data.approvals) ? (data.approvals as ApprovalItem[]) : [],
            events: Array.isArray(data.events) ? (data.events as AgentEvent[]) : [],
          });
        }
        break;
      case 'node':
        if (isRecord(data) && typeof data.id === 'string') {
          setSnapshot((s) => ({ ...s, nodes: upsertById(s.nodes, data as unknown as AgentNode) }));
        }
        break;
      case 'task':
        if (isRecord(data) && typeof data.id === 'number') {
          setSnapshot((s) => ({ ...s, tasks: upsertById(s.tasks, data as unknown as AgentTask) }));
        }
        break;
      case 'approval':
        if (isRecord(data) && typeof data.id === 'string') {
          setSnapshot((s) => ({
            ...s,
            approvals: upsertById(s.approvals, data as unknown as ApprovalItem),
          }));
        }
        break;
      case 'log':
        if (isRecord(data) && typeof data.id === 'string') {
          setSnapshot((s) => ({
            ...s,
            events: [data as unknown as AgentEvent, ...s.events].slice(0, MAX_LOG_EVENTS),
          }));
        }
        break;
      default:
        break; // ping / 未知事件忽略
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let cancelled = false;
    let retryMs = 1_000;

    async function fetchSnapshot(): Promise<boolean> {
      try {
        const res = await fetch(`${API_BASE}/agent-console/snapshot`, {
          credentials: 'same-origin',
          signal: controller.signal,
        });
        if (!res.ok) return false;
        const data: unknown = await res.json();
        if (isRecord(data)) {
          applyEvent({ event: 'snapshot', data });
          return true;
        }
        return false;
      } catch {
        return false;
      }
    }

    async function streamOnce(): Promise<void> {
      const res = await fetch(`${API_BASE}/agent-console/events`, {
        credentials: 'same-origin',
        headers: { Accept: 'text/event-stream' },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`SSE ${res.status}`);
      everConnectedRef.current = true;
      setConnection('live');
      setLoading(false);
      const parser = createSseParser();
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value, { stream: true });
        for (const ev of parser.feed(text)) applyEvent(ev);
      }
      for (const ev of parser.flush()) applyEvent(ev);
    }

    async function run() {
      const ok = await fetchSnapshot();
      if (ok) {
        everConnectedRef.current = true;
        setLoading(false);
      }
      while (!cancelled) {
        try {
          await streamOnce();
        } catch {
          if (cancelled) return;
        }
        if (cancelled) return;
        setConnection(everConnectedRef.current ? 'reconnecting' : 'offline');
        setLoading(false);
        await new Promise((r) => setTimeout(r, retryMs));
        retryMs = Math.min(retryMs * 2, 15_000);
        // 重连前先补一份全量，避免断线期间的增量丢失
        await fetchSnapshot();
      }
    }

    void run();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [enabled, refreshTick, applyEvent]);

  const refresh = useCallback(() => setRefreshTick((n) => n + 1), []);

  const decideApproval = useCallback(async (id: string, decision: ApprovalDecision) => {
    const res = await fetch(`${API_BASE}/agent-console/approvals/${encodeURIComponent(id)}/decision`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    });
    if (!res.ok) throw new Error(`decision ${res.status}`);
    const data: unknown = await res.json().catch(() => null);
    setSnapshot((s) => {
      const updated =
        isRecord(data) && typeof data.id === 'string'
          ? (data as unknown as ApprovalItem)
          : s.approvals.find((a) => a.id === id);
      if (!updated) return s;
      const item: ApprovalItem =
        updated.id === id && updated.status === 'pending'
          ? { ...updated, status: decision, decidedAt: new Date().toISOString() }
          : updated;
      // 审批记事件：本地先落一条日志（服务端也会经 SSE 推回同 id 事件，去重靠 id）
      const logEvent: AgentEvent = {
        id: `local-approval-${id}-${item.decidedAt ?? ''}`,
        at: item.decidedAt ?? new Date().toISOString(),
        agentId: 'console',
        type: 'APPROVAL_DECIDED',
        taskId: item.taskId,
        message: item.title,
      };
      return {
        ...s,
        approvals: upsertById(s.approvals, item),
        events: [logEvent, ...s.events].slice(0, MAX_LOG_EVENTS),
      };
    });
  }, []);

  return {
    nodes: snapshot.nodes,
    tasks: snapshot.tasks,
    events: snapshot.events,
    approvals: snapshot.approvals,
    connection,
    loading,
    refresh,
    decideApproval,
  };
}
