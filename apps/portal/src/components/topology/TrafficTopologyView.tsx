import { useEffect, useMemo, useState } from 'react';
import { Activity, Building2, HelpCircle, RefreshCw, Server, Share2, TriangleAlert, type LucideIcon } from 'lucide-react';
import { useI18n } from '../../i18n';
import ZoomStage from './ZoomStage';
import { trafficTopologyMock, type TrafficLink, type TrafficNode, type TrafficNodeType, type TrafficPeriod, type TrafficTopologyData } from './trafficTopology.mock';

// 流量拓扑（Issue #230 Phase 1）：PRTG 链路传感器利用率视图。
// 数据优先 GET /api/network-topology/traffic；接口失败或返回空时回退 Mock，并挂显式 Mock 徽标。
// 容量缺失的链路只按绝对流量降级着色（中性灰 + Mbps 标签），**绝不**套用利用率百分比色。
// 画布复用 ZoomStage（平移/缩放），坐标沿用后端 0-1000 相对坐标。
const PERIODS: TrafficPeriod[] = ['live', '1h', '24h', '7d'];
const PERIOD_KEYS: Record<TrafficPeriod, string> = { live: 'trafficTopo.periodLive', '1h': 'trafficTopo.period1h', '24h': 'trafficTopo.period24h', '7d': 'trafficTopo.period7d' };
const NODE_TYPE_KEYS: Record<TrafficNodeType, string> = { device: 'trafficTopo.nodeTypeDevice', peer: 'trafficTopo.nodeTypePeer', customer: 'trafficTopo.nodeTypeCustomer', unknown: 'trafficTopo.nodeTypeUnknown' };
const STATUS_KEYS: Record<string, string> = { up: 'trafficTopo.statusUp', unusual: 'trafficTopo.statusUnusual', down: 'trafficTopo.statusDown', unknown: 'trafficTopo.statusUnknown' };
const CONF_KEYS: Record<string, string> = { verified: 'trafficTopo.confVerified', candidate: 'trafficTopo.confCandidate', discovered: 'trafficTopo.confDiscovered' };
const NODE_ICONS: Record<TrafficNodeType, LucideIcon> = { device: Server, peer: Share2, customer: Building2, unknown: HelpCircle };
const STATUS_COLORS: Record<string, string> = { up: '#16a34a', unusual: '#d97706', down: '#dc2626', unknown: '#94a3b8' };
// 8 档利用率配色：紫 → 红；>100% 单独用深红，并配 OVER CAPACITY 文案。
const BANDS: { limit: number; color: string; key: string }[] = [
  { limit: 1, color: '#7c3aed', key: 'trafficTopo.band0' }, { limit: 10, color: '#8b5cf6', key: 'trafficTopo.band1' },
  { limit: 25, color: '#6366f1', key: 'trafficTopo.band2' }, { limit: 40, color: '#3b82f6', key: 'trafficTopo.band3' },
  { limit: 55, color: '#0ea5e9', key: 'trafficTopo.band4' }, { limit: 70, color: '#f59e0b', key: 'trafficTopo.band5' },
  { limit: 85, color: '#f97316', key: 'trafficTopo.band6' }, { limit: 100, color: '#ef4444', key: 'trafficTopo.band7' },
];
const OVER_COLOR = '#991b1b';
const UNKNOWN_CAPACITY_COLOR = '#94a3b8';
function normStatus(status: string | null | undefined): string {
  const v = (status ?? '').toLowerCase();
  return v === 'up' || v === 'unusual' || v === 'down' ? v : 'unknown';
}
function hasCapacity(link: TrafficLink): boolean {
  // 单一真相源：优先用后端 data_quality.capacity_known；Mock 也填了该字段。
  return link.data_quality?.capacity_known ?? ((link.capacity_ab_mbps ?? 0) > 0 || (link.capacity_ba_mbps ?? 0) > 0);
}
// 利用率只消费后端算好的 utilization_pct，前端不重算（Issue #230 硬口径）。
function utilizationOf(link: TrafficLink): number | null {
  return link.utilization_pct ?? null;
}
function bandOf(util: number) {
  return BANDS.find((b) => util <= b.limit) ?? null;
}
function fmtNumber(v: number, digits = 0): string {
  return v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: digits });
}
function fmtMbps(v: number | null | undefined): string {
  return v == null ? '—' : fmtNumber(v, v < 10 ? 1 : 0);
}
function withUnit(v: number | null | undefined): string {
  return v == null ? '—' : `${fmtMbps(v)} Mbps`;
}
function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v < 10 ? v.toFixed(1) : Math.round(v)}%`;
}
function fmtAge(sec: number | null | undefined): string {
  if (sec == null || Number.isNaN(sec)) return '—';
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  return `${(sec / 3600).toFixed(1)}h`;
}
function shortName(name: string, max = 18): string {
  return name.length > max ? `${name.slice(0, max)}…` : name;
}
function textWidth(s: string): number {
  let w = 0;
  for (const ch of s) w += /[\u4e00-\u9fff]/.test(ch) ? 11 : 6.6;
  return w;
}
function isTrafficData(v: unknown): v is TrafficTopologyData {
  if (!v || typeof v !== 'object') return false;
  const d = v as Partial<TrafficTopologyData>;
  return Array.isArray(d.nodes) && d.nodes.length > 0 && Array.isArray(d.links) && d.links.length > 0;
}
function Kpi({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 shadow-sm">
      <div className="text-[11px] text-slate-500 dark:text-slate-400">{label}</div>
      <div className={`text-[18px] font-bold leading-tight ${tone ?? 'text-slate-900 dark:text-white'}`}>{value}</div>
    </div>
  );
}
function InfoRow({ label, value, warning }: { label: string; value: string; warning?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1 border-b border-slate-100 dark:border-slate-800 last:border-0">
      <span className="text-[12px] text-slate-500 dark:text-slate-400">{label}</span>
      <span className={`text-[12.5px] font-medium text-right ${warning ? 'text-amber-600 dark:text-amber-400' : 'text-slate-700 dark:text-slate-200'}`}>{value}</span>
    </div>
  );
}

export default function TrafficTopologyView() {
  const { t } = useI18n();
  const [period, setPeriod] = useState<TrafficPeriod>('live');
  const [refreshToken, setRefreshToken] = useState(0);
  const [data, setData] = useState<TrafficTopologyData | null>(null);
  const [usingMock, setUsingMock] = useState(false);
  const [loading, setLoading] = useState(true);
  const [deviceId, setDeviceId] = useState<number | null>(null);
  const [selectedLinkId, setSelectedLinkId] = useState<number | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    let alive = true;
    setLoading(true);
    fetch(`/api/network-topology/traffic?period=${period}`, { signal: ctrl.signal, headers: { Accept: 'application/json' } })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<unknown>;
      })
      .then((json) => {
        if (!alive) return;
        if (!isTrafficData(json)) throw new Error('empty or malformed payload');
        const payload = json;
        setData(payload);
        setUsingMock(false);
        setSelectedLinkId((id) => (payload.links.some((l) => l.id === id) ? id : null));
      })
      .catch((err: unknown) => {
        if (!alive || (err instanceof DOMException && err.name === 'AbortError')) return;
        // 接口失败/为空：回退 Mock，并让 Mock 徽标常驻可见，绝不冒充真实数据。
        setData(trafficTopologyMock(period));
        setUsingMock(true);
        setSelectedLinkId(null);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
      ctrl.abort();
    };
  }, [period, refreshToken]);

  const nodeById = useMemo(() => new Map((data?.nodes ?? []).map((n) => [n.id, n])), [data]);
  const deviceNodes = useMemo(() => (data?.nodes ?? []).filter((n) => n.type === 'device'), [data]);
  const links = useMemo(() => {
    if (!data) return [];
    const byDevice = deviceId == null ? data.links : data.links.filter((l) => l.source_node === deviceId || l.target_node === deviceId);
    return byDevice.filter((l) => nodeById.has(l.source_node) && nodeById.has(l.target_node));
  }, [data, deviceId, nodeById]);
  const nodes = useMemo(() => {
    if (!data) return [];
    if (deviceId == null) return data.nodes;
    const ids = new Set<number>();
    links.forEach((l) => {
      ids.add(l.source_node);
      ids.add(l.target_node);
    });
    return data.nodes.filter((n) => ids.has(n.id));
  }, [data, deviceId, links]);

  const kpi = useMemo(() => {
    let up = 0;
    let unusual = 0;
    let down = 0;
    let over = 0;
    let capKnown = 0;
    let unknown = 0;
    for (const l of links) {
      const s = normStatus(l.status);
      if (s === 'up') up += 1;
      else if (s === 'unusual') unusual += 1;
      else if (s === 'down') down += 1;
      else unknown += 1;
      const u = utilizationOf(l);
      if (u != null && u > 100) over += 1;
      if (hasCapacity(l)) capKnown += 1;
    }
    return { nodes: nodes.length, links: links.length, up, unusual, down, unknown, over, capKnown };
  }, [links, nodes.length]);

  const selected = links.find((l) => l.id === selectedLinkId) ?? null;
  const sourceNode = selected ? nodeById.get(selected.source_node) ?? null : null;
  const targetNode = selected ? nodeById.get(selected.target_node) ?? null : null;

  // 链路绘制口径：有容量 → 8 档利用率色；无容量 → 灰阶 + 绝对流量线宽 + Mbps 标签。
  const paintLink = (link: TrafficLink) => {
    const util = utilizationOf(link);
    const noTraffic = link.in_mbps == null && link.out_mbps == null;
    if (noTraffic) return { color: UNKNOWN_CAPACITY_COLOR, width: 2, label: '—', sub: null as string | null, dashed: true };
    if (!hasCapacity(link)) {
      const total = (link.in_mbps ?? 0) + (link.out_mbps ?? 0);
      const width = total >= 500 ? 5 : total >= 200 ? 4 : total >= 50 ? 3 : 2;
      return { color: UNKNOWN_CAPACITY_COLOR, width, label: `${fmtNumber(total, total < 10 ? 1 : 0)} Mbps`, sub: t('trafficTopo.capacityUnknown'), dashed: true };
    }
    if (util == null) return { color: UNKNOWN_CAPACITY_COLOR, width: 2, label: '—', sub: null as string | null, dashed: true };
    if (util > 100) return { color: OVER_COLOR, width: 5.5, label: fmtPct(util), sub: t('trafficTopo.overCapacity'), dashed: false };
    const band = bandOf(util) ?? BANDS[BANDS.length - 1];
    return { color: band.color, width: 2 + (Math.min(util, 100) / 100) * 2.2, label: fmtPct(util), sub: null as string | null, dashed: false };
  };

  const sampleAge = selected?.data_quality?.sample_age_seconds ?? null;
  const prtgDown = selected != null && selected.data_quality?.traffic_available === false;
  const stale = !prtgDown && sampleAge != null && sampleAge > 600;

  return (
    <div className="space-y-3">
      {/* 顶部 Toolbar：设备筛选 / 时间范围 / 刷新 + 数据来源与 Mock 徽标 */}
      <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl px-3 py-3 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-[15px] font-bold flex items-center gap-2 text-slate-900 dark:text-white">
            <Activity className="w-4 h-4 text-[#ff7900]" />
            {t('trafficTopo.title')}
          </h2>
          {usingMock ? (
            <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
              {t('trafficTopo.mockBadge')}
            </span>
          ) : (
            <span className="text-[11px] text-slate-500 dark:text-slate-400">
              {t('trafficTopo.sourcePrtg')} · {t('trafficTopo.dataAge')} {fmtAge(data?.data_age_seconds)}
            </span>
          )}
          <button
            type="button"
            onClick={() => setRefreshToken((v) => v + 1)}
            disabled={loading}
            title={t('trafficTopo.refreshTitle')}
            aria-label={t('trafficTopo.refreshTitle')}
            className="ml-auto inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 text-[12.5px] text-slate-600 dark:text-slate-300 disabled:opacity-60"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            {t('trafficTopo.refresh')}
          </button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <label htmlFor="traffic-device-filter" className="text-[12px] text-slate-500 dark:text-slate-400">{t('trafficTopo.filterDevice')}</label>
          <select
            id="traffic-device-filter"
            value={deviceId ?? ''}
            onChange={(e) => {
              setDeviceId(e.target.value ? Number(e.target.value) : null);
              setSelectedLinkId(null);
            }}
            className="px-2 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-[12.5px] bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-200"
          >
            <option value="">{t('trafficTopo.allDevices')}</option>
            {deviceNodes.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
          </select>
          <div className="inline-flex rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
            {PERIODS.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setPeriod(p)}
                aria-pressed={period === p}
                className={`px-2.5 py-1.5 text-[12px] transition-colors ${period === p ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900' : 'bg-white dark:bg-slate-900 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800'}`}
              >
                {t(PERIOD_KEYS[p])}
              </button>
            ))}
          </div>
        </div>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-slate-500 dark:text-slate-400">{t('trafficTopo.subtitle')}</p>
        {usingMock && <p className="mt-1 text-[11.5px] leading-relaxed text-amber-700 dark:text-amber-300">{t('trafficTopo.mockHint')}</p>}
      </section>

      {/* KPI 条 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-2">
        <Kpi label={t('trafficTopo.kpiNodes')} value={String(kpi.nodes)} />
        <Kpi label={t('trafficTopo.kpiLinks')} value={String(kpi.links)} />
        <Kpi label={t('trafficTopo.kpiUp')} value={String(kpi.up)} tone="text-emerald-600 dark:text-emerald-400" />
        <Kpi label={t('trafficTopo.kpiUnusual')} value={String(kpi.unusual)} tone="text-amber-600 dark:text-amber-400" />
        <Kpi label={t('trafficTopo.kpiDown')} value={String(kpi.down)} tone="text-red-600 dark:text-red-400" />
        <Kpi label={t('trafficTopo.kpiUnknown')} value={String(kpi.unknown)} tone="text-slate-500 dark:text-slate-400" />
        <Kpi label={t('trafficTopo.kpiOver')} value={String(kpi.over)} tone="text-red-600 dark:text-red-400" />
        <Kpi label={t('trafficTopo.kpiCapKnown')} value={`${kpi.capKnown}/${kpi.links}`} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,3fr)_minmax(300px,1fr)] gap-3 items-start">
        {/* 主画布约 75% */}
        <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden shadow-sm">
          <div className="flex flex-wrap items-center gap-2 px-3 pt-3">
            <h3 className="text-[13.5px] font-semibold text-slate-800 dark:text-slate-100">{t('trafficTopo.canvasTitle')}</h3>
            {selected && <span className="text-[11.5px] text-slate-500 dark:text-slate-400">{selected.key} · {fmtPct(utilizationOf(selected))}</span>}
            {loading && data && <span className="text-[11.5px] text-slate-400">{t('trafficTopo.loading')}</span>}
          </div>
          <div className="mt-2.5 border-t border-slate-100 dark:border-slate-800">
            {!data ? (
              <div className="h-[58vh] min-h-[430px] flex items-center justify-center text-[13px] text-slate-500 dark:text-slate-400">{t('trafficTopo.loading')}</div>
            ) : (
              <ZoomStage
                baseW={1000}
                baseH={620}
                heightClass="h-[58vh] min-h-[430px]"
                zoomInLabel={t('topo.zoomIn')}
                zoomOutLabel={t('topo.zoomOut')}
                resetLabel={t('topo.zoomReset')}
                fullLabel={t('topo.zoomFull')}
              >
                <svg
                  width={1000}
                  height={620}
                  viewBox="0 0 1000 620"
                  className="block bg-white dark:bg-slate-900"
                  onClick={() => setSelectedLinkId(null)}
                  role="img"
                  aria-label={t('trafficTopo.canvasTitle')}
                >
                  {links.length === 0 && (
                    <text x={500} y={310} textAnchor="middle" fontSize={18} className="fill-slate-400">{t('trafficTopo.emptyFiltered')}</text>
                  )}
                  {links.map((link) => {
                    const a = nodeById.get(link.source_node);
                    const b = nodeById.get(link.target_node);
                    if (!a || !b) return null;
                    const paint = paintLink(link);
                    const mx = (a.x + b.x) / 2;
                    const my = (a.y + b.y) / 2;
                    const lines = paint.sub ? [paint.label, paint.sub] : [paint.label];
                    const boxW = Math.max(...lines.map(textWidth)) + 14;
                    const boxH = lines.length * 13 + 7;
                    return (
                      <g
                        key={link.id}
                        role="button"
                        tabIndex={0}
                        aria-label={`${link.source ?? ''} → ${link.target ?? ''} ${paint.label}`}
                        className="cursor-pointer"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedLinkId(link.id);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            setSelectedLinkId(link.id);
                          }
                        }}
                      >
                        <title>{`${link.key} · ${paint.label}`}</title>
                        {selected?.id === link.id && (
                          <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#f97316" strokeWidth={paint.width + 7} opacity={0.28} strokeLinecap="round" />
                        )}
                        <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="transparent" strokeWidth={16} strokeLinecap="round" />
                        <line
                          x1={a.x}
                          y1={a.y}
                          x2={b.x}
                          y2={b.y}
                          stroke={paint.color}
                          strokeWidth={paint.width}
                          strokeDasharray={paint.dashed ? '7 4' : undefined}
                          strokeLinecap="round"
                          opacity={0.92}
                        />
                        <g pointerEvents="none">
                          <rect x={mx - boxW / 2} y={my - boxH / 2} width={boxW} height={boxH} rx={4} className="fill-white dark:fill-slate-900" opacity={0.9} />
                          {lines.map((line, i) => (
                            <text key={i} x={mx} y={my - boxH / 2 + 13 + i * 13} textAnchor="middle" fontSize={10.5} className={paint.dashed ? 'fill-slate-500 dark:fill-slate-400' : 'fill-slate-700 dark:fill-slate-200'}>
                              {line}
                            </text>
                          ))}
                        </g>
                      </g>
                    );
                  })}
                  {nodes.map((n: TrafficNode) => {
                    const Icon = NODE_ICONS[n.type] ?? HelpCircle;
                    const status = normStatus(n.status);
                    return (
                      <g key={n.id} role="img" aria-label={`${t(NODE_TYPE_KEYS[n.type])} ${n.name} ${t(STATUS_KEYS[status])}`}>
                        <title>{`${n.name} · ${t(NODE_TYPE_KEYS[n.type])} · ${t(STATUS_KEYS[status])}`}</title>
                        <circle cx={n.x} cy={n.y} r={22} className="fill-white dark:fill-slate-900" stroke={STATUS_COLORS[status]} strokeWidth={2.5} />
                        <g transform={`translate(${n.x - 9}, ${n.y - 9})`}>
                          <Icon size={18} color={STATUS_COLORS[status]} strokeWidth={2.2} />
                        </g>
                        <text x={n.x} y={n.y + 34} textAnchor="middle" fontSize={11} fontWeight={600} className="fill-slate-700 dark:fill-slate-200">{shortName(n.name)}</text>
                      </g>
                    );
                  })}
                </svg>
              </ZoomStage>
            )}
          </div>
          <p className="px-3 py-2 text-[12px] text-slate-500 dark:text-slate-400 border-t border-slate-100 dark:border-slate-800">{t('trafficTopo.canvasHint')}</p>
        </section>

        {/* 右侧详情面板约 25% */}
        <aside className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-3 shadow-sm xl:sticky xl:top-3">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-[14px] font-bold text-slate-900 dark:text-white">{t('trafficTopo.detailTitle')}</h3>
            {selected && (
              <button
                type="button"
                onClick={() => setSelectedLinkId(null)}
                title={t('trafficTopo.clearSelection')}
                aria-label={t('trafficTopo.clearSelection')}
                className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 text-[16px] leading-none px-1"
              >
                ×
              </button>
            )}
          </div>
          {!selected && <p className="mt-2 text-[12.5px] leading-relaxed text-slate-500 dark:text-slate-400">{t('trafficTopo.detailEmpty')}</p>}
          {selected && (
            <div className="mt-2 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <b className="text-[13px] text-slate-800 dark:text-slate-100 break-all">{selected.key}</b>
                <span className="text-[10.5px] font-semibold px-1.5 py-0.5 rounded-full text-white" style={{ backgroundColor: STATUS_COLORS[normStatus(selected.status)] }}>
                  {t(STATUS_KEYS[normStatus(selected.status)])}
                </span>
              </div>
              <div>
                <div className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('trafficTopo.endpoints')}</div>
                <div className="space-y-1">
                  {[sourceNode, targetNode].map((n, i) => (
                    <div key={i} className="flex items-center gap-2 text-[12.5px]">
                      <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: STATUS_COLORS[normStatus(n?.status)] }} />
                      <span className="text-slate-700 dark:text-slate-200 font-medium">{n?.name ?? '—'}</span>
                      {n && <span className="text-slate-400 text-[11px]">{t(NODE_TYPE_KEYS[n.type])}</span>}
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <InfoRow label={t('trafficTopo.statusLabel')} value={t(STATUS_KEYS[normStatus(selected.status)])} />
                <InfoRow label={t('trafficTopo.inMbps')} value={withUnit(selected.in_mbps)} />
                <InfoRow label={t('trafficTopo.outMbps')} value={withUnit(selected.out_mbps)} />
                <InfoRow label={t('trafficTopo.capacityAB')} value={(selected.capacity_ab_mbps ?? 0) <= 0 ? t('trafficTopo.capacityPending') : withUnit(selected.capacity_ab_mbps)} warning={(selected.capacity_ab_mbps ?? 0) <= 0} />
                <InfoRow label={t('trafficTopo.capacityBA')} value={(selected.capacity_ba_mbps ?? 0) <= 0 ? t('trafficTopo.capacityPending') : withUnit(selected.capacity_ba_mbps)} warning={(selected.capacity_ba_mbps ?? 0) <= 0} />
                <InfoRow label={t('trafficTopo.utilization')} value={fmtPct(utilizationOf(selected))} />
                <InfoRow label={t('trafficTopo.mappingConfidence')} value={t(CONF_KEYS[selected.mapping_confidence] ?? 'trafficTopo.confDiscovered')} />
                <InfoRow label={t('trafficTopo.linkTypeLabel')} value={selected.link_type || '—'} />
                <InfoRow label={t('trafficTopo.sensorLabel')} value={selected.sensor_id == null ? '—' : String(selected.sensor_id)} />
              </div>
              <div className="pt-1">
                <div className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('trafficTopo.dataQuality')}</div>
                <InfoRow label={t('trafficTopo.capacityKnown')} value={hasCapacity(selected) ? t('trafficTopo.yes') : t('trafficTopo.no')} warning={!hasCapacity(selected)} />
                <InfoRow label={t('trafficTopo.sampleAge')} value={fmtAge(sampleAge)} warning={stale} />
              </div>
              {prtgDown && (
                <p className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 rounded-lg px-2 py-1.5">
                  <TriangleAlert className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  {t('trafficTopo.warnPrtgDown')}
                </p>
              )}
              {stale && (
                <p className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 rounded-lg px-2 py-1.5">
                  <TriangleAlert className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  {t('trafficTopo.warnStale', { age: fmtAge(sampleAge) })}
                </p>
              )}
            </div>
          )}
        </aside>
      </div>

      {/* 底部固定 Traffic Load 图例：8 档色块 + 容量未知灰条 + OVER CAPACITY */}
      <div className="sticky bottom-2 z-20 rounded-2xl border border-slate-200 dark:border-slate-800 bg-white/95 dark:bg-slate-900/95 backdrop-blur px-3 py-2 shadow-sm">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11.5px] text-slate-600 dark:text-slate-300">
          <span className="font-semibold text-slate-800 dark:text-slate-100">{t('trafficTopo.legendTitle')}</span>
          {BANDS.map((b) => (
            <span key={b.key} className="inline-flex items-center gap-1">
              <span className="w-5 h-2 rounded-sm" style={{ backgroundColor: b.color }} />
              {t(b.key)}
            </span>
          ))}
          <span className="inline-flex items-center gap-1">
            <span className="w-5 h-2 rounded-sm" style={{ backgroundColor: UNKNOWN_CAPACITY_COLOR }} />
            {t('trafficTopo.legendUnknown')}
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="w-5 h-2 rounded-sm" style={{ backgroundColor: OVER_COLOR }} />
            {t('trafficTopo.legendOver')}
          </span>
        </div>
      </div>
    </div>
  );
}
