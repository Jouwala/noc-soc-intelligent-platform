import { useState } from 'react';
import { Plus, RefreshCw, Trash2, ArrowLeftRight, Save } from 'lucide-react';
import { useI18n } from '../../i18n';
import type { TrafficLink, TrafficNode } from './trafficTopology.mock';

// Phase 2 管理面板（Issue #230）：新增/删除节点与链路、绑定 Traffic 传感器、
// 分方向容量、上下游确认。所有写接口仅 admin；失败一律显示服务端错误，不静默。
interface SensorOption { objid: number; name: string; device: string | null; bound: boolean }

interface Props {
  nodes: TrafficNode[];
  links: TrafficLink[];
  onReload: () => void;
}

const API = '/api/network-topology';

async function send(path: string, method: string, body?: unknown): Promise<unknown> {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const data = await res.json();
      detail = typeof data?.detail === 'string' ? data.detail : JSON.stringify(data?.detail ?? data);
    } catch {
      /* 非 JSON 错误体，保留状态码 */
    }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

export default function TrafficTopologyAdmin({ nodes, links, onReload }: Props) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sensors, setSensors] = useState<SensorOption[]>([]);
  const [nodeName, setNodeName] = useState('');
  const [nodeType, setNodeType] = useState<'peer' | 'customer' | 'unknown'>('peer');
  const [sensorId, setSensorId] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [targetId, setTargetId] = useState('');
  const [caps, setCaps] = useState<Record<number, { ab: string; ba: string }>>({});

  const loadSensors = async () => {
    try {
      const res = await fetch(`${API}/sensors`);
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      setSensors(Array.isArray(data.sensors) ? data.sensors : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await fn();
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const addNode = () => run(async () => {
    if (!nodeName.trim()) throw new Error(t('trafficTopo.admin.nameRequired'));
    await send('/nodes', 'POST', { name: nodeName.trim(), type: nodeType });
    setNodeName('');
  });

  const addLink = () => run(async () => {
    if (!sensorId || !sourceId || !targetId) throw new Error(t('trafficTopo.admin.selectRequired'));
    await send('/links', 'POST', {
      sensor_objid: Number(sensorId),
      source_node_id: Number(sourceId),
      target_node_id: Number(targetId),
      capacity_source: 'manual',
    });
    setSensorId('');
    await loadSensors();
  });

  const saveCaps = (link: TrafficLink) => run(async () => {
    const value = caps[link.id] ?? {
      ab: link.capacity_ab_mbps == null ? '' : String(link.capacity_ab_mbps),
      ba: link.capacity_ba_mbps == null ? '' : String(link.capacity_ba_mbps),
    };
    for (const raw of [value?.ab, value?.ba]) {
      if (raw !== undefined && raw !== '' && !Number.isFinite(Number(raw))) {
        throw new Error(t('trafficTopo.admin.capacityInvalid'));
      }
    }
    await send('/mappings', 'PUT', {
      links: [{
        id: link.id,
        capacity_ab_mbps: value?.ab === '' || value?.ab === undefined ? null : Number(value.ab),
        capacity_ba_mbps: value?.ba === '' || value?.ba === undefined ? null : Number(value.ba),
        capacity_source: 'manual',
      }],
    });
  });

  const unbound = sensors.filter((s) => !s.bound);

  return (
    <section className="bg-white dark:bg-slate-900 border border-amber-200 dark:border-amber-800/60 rounded-2xl p-3 shadow-sm space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-[13.5px] font-semibold text-slate-800 dark:text-slate-100">{t('trafficTopo.admin.title')}</h3>
        <button type="button" onClick={loadSensors} disabled={busy}
          className="ml-auto inline-flex items-center gap-1 text-[12px] px-2 py-1 rounded-lg border border-slate-200 dark:border-slate-700">
          <RefreshCw size={13} /> {t('trafficTopo.admin.loadSensors')}
        </button>
      </div>
      {error && <div className="text-[12px] text-red-600 dark:text-red-400">{t('trafficTopo.admin.error')}: {error}</div>}

      {/* 新增节点 */}
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-[11.5px] text-slate-500 dark:text-slate-400">{t('trafficTopo.admin.nodeName')}
          <input value={nodeName} onChange={(e) => setNodeName(e.target.value)}
            className="ml-1 px-2 py-1 text-[12px] rounded border border-slate-200 dark:border-slate-700 bg-transparent" />
        </label>
        <select value={nodeType} onChange={(e) => setNodeType(e.target.value as typeof nodeType)}
          className="px-2 py-1 text-[12px] rounded border border-slate-200 dark:border-slate-700 bg-transparent">
          <option value="peer">{t('trafficTopo.nodeTypePeer')}</option>
          <option value="customer">{t('trafficTopo.nodeTypeCustomer')}</option>
          <option value="unknown">{t('trafficTopo.nodeTypeUnknown')}</option>
        </select>
        <button type="button" onClick={addNode} disabled={busy}
          className="inline-flex items-center gap-1 text-[12px] px-2 py-1 rounded-lg bg-emerald-600 text-white disabled:opacity-50">
          <Plus size={13} /> {t('trafficTopo.admin.addNode')}
        </button>
      </div>

      {/* 节点列表：删除（仍被链路引用时服务端返回 409） */}
      <div className="flex flex-wrap gap-1.5 border-t border-slate-100 dark:border-slate-800 pt-2">
        {nodes.map((n) => (
          <span key={n.id} className="inline-flex items-center gap-1 text-[11.5px] px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700">
            {n.name}
            <button type="button" disabled={busy} onClick={() => run(() => send(`/nodes/${n.id}`, 'DELETE'))}
              className="text-red-600 dark:text-red-400" aria-label={t('trafficTopo.admin.deleteNode')}>
              <Trash2 size={11} />
            </button>
          </span>
        ))}
      </div>

      {/* 绑定传感器新增链路 */}
      <div className="flex flex-wrap items-end gap-2">
        <select value={sensorId} onChange={(e) => setSensorId(e.target.value)}
          className="px-2 py-1 text-[12px] rounded border border-slate-200 dark:border-slate-700 bg-transparent max-w-[260px]">
          <option value="">{t('trafficTopo.admin.pickSensor')}</option>
          {unbound.map((s) => (
            <option key={s.objid} value={s.objid}>{s.device ? `${s.device} / ` : ''}{s.name} ({s.objid})</option>
          ))}
        </select>
        <select value={sourceId} onChange={(e) => setSourceId(e.target.value)}
          className="px-2 py-1 text-[12px] rounded border border-slate-200 dark:border-slate-700 bg-transparent">
          <option value="">{t('trafficTopo.admin.pickSource')}</option>
          {nodes.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
        </select>
        <select value={targetId} onChange={(e) => setTargetId(e.target.value)}
          className="px-2 py-1 text-[12px] rounded border border-slate-200 dark:border-slate-700 bg-transparent">
          <option value="">{t('trafficTopo.admin.pickTarget')}</option>
          {nodes.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
        </select>
        <button type="button" onClick={addLink} disabled={busy}
          className="inline-flex items-center gap-1 text-[12px] px-2 py-1 rounded-lg bg-emerald-600 text-white disabled:opacity-50">
          <Plus size={13} /> {t('trafficTopo.admin.addLink')}
        </button>
      </div>

      {/* 现有链路：容量 / 上下游 / 删除 */}
      <div className="max-h-[320px] overflow-auto border-t border-slate-100 dark:border-slate-800 pt-2">
        {links.map((link) => {
          const value = caps[link.id] ?? {
            ab: link.capacity_ab_mbps == null ? '' : String(link.capacity_ab_mbps),
            ba: link.capacity_ba_mbps == null ? '' : String(link.capacity_ba_mbps),
          };
          return (
            <div key={link.id} className="flex flex-wrap items-center gap-1.5 py-1 border-b border-slate-100 dark:border-slate-800 last:border-0 text-[12px]">
              <span className="min-w-[220px] truncate" title={`${link.source} → ${link.target}`}>{link.source} → {link.target}</span>
              <input value={value.ab} placeholder={t('trafficTopo.capacityAB')} onChange={(e) => setCaps((p) => ({ ...p, [link.id]: { ...value, ab: e.target.value } }))}
                className="w-[86px] px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700 bg-transparent" />
              <input value={value.ba} placeholder={t('trafficTopo.capacityBA')} onChange={(e) => setCaps((p) => ({ ...p, [link.id]: { ...value, ba: e.target.value } }))}
                className="w-[86px] px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700 bg-transparent" />
              <button type="button" onClick={() => saveCaps(link)} disabled={busy} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700">
                <Save size={12} /> {t('trafficTopo.admin.saveCaps')}
              </button>
              <button type="button" onClick={() => run(() => send(`/links/${link.id}/direction`, 'POST'))} disabled={busy}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700">
                <ArrowLeftRight size={12} /> {t('trafficTopo.admin.swap')}
              </button>
              <button type="button" onClick={() => run(() => send(`/links/${link.id}`, 'DELETE'))} disabled={busy}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400">
                <Trash2 size={12} /> {t('trafficTopo.admin.delete')}
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
}
