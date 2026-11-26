/* ============================================================================
 * location/MwLinkDrawer —— 位置页点 MW 链路后的「链路详情」抽屉
 * ============================================================================
 *
 * 需求（2026-11-23）：点击链路的效果与 `?page=mwRealTime` 点链路一致。
 *
 * 实现取舍：
 *   · **画**链路用 /api/gis/topology?with_util=1（与 GIS 页同一份 KML 链路 +
 *     同一 15 分钟利用率槽位），因此地图上的位置/颜色与 GIS 完全一致；
 *   · **看**明细用 /api/mw-ume/realtime（与 mw_real_time.html 同一接口）：
 *     抽屉里展示链路状态、总容量、两端站点/Node IP/接口、TX/RX 吞吐与利用率、
 *     24 小时（最近 96 个 15 分钟槽）趋势、端口级明细 —— 字段与 mwRealTime 的
 *     抽屉一一对应。
 *
 * ⚠️ 两套数据主键不同（KML 用站名、KPI 用节点 IP），实测只有约一半 KML 链路
 *    能匹配到实时明细。匹配不上时**如实说明「无 KPI 明细」并保留 KML 结构信息**
 *    （端点、站名、GIS 侧利用率），不能伪造一条别人的链路，也不能把「不知道」
 *    画成绿色 —— 与 gis_mw_overlay.py 的「宁可少标绝不猜标」同一条纪律。
 *
 * ⚠️ /api/mw-ume/realtime 响应约 2.8MB，只在**第一次点链路**时拉取，模块级缓存；
 *    之后点其他链路不再重复下载。
 * ========================================================================== */
import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { Alert, Descriptions, Drawer, Empty, Spin, Tag, Typography } from 'antd'

import api from '../../api'
import { useI18n } from '../../i18n'
import { useTheme } from '../../theme'
import { fmtKm, distanceToSegmentKm, haversineKm } from './geo'
import type { TopologyLink, UtilLevel } from '../gismap/types'
import { UTIL_LEVEL_COLORS, utilLevelOf } from '../gismap/types'

const { Text } = Typography

/* echarts 只在抽屉真的画出趋势时才加载（与 gismap/ChartBlock 同一做法）。 */
const ReactECharts = lazy(() => import('../../components/ECharts'))

type MwNode = {
  id: string
  name?: string
  site?: string
  site_id?: string
  ip?: string
  lat?: number
  lon?: number
}
type MwCheckpoint = { cp?: string; ncp?: string; tx_t?: string; tx_u?: string; rx_t?: string; rx_u?: string }
type MwHistory = { date: string; tx_util?: string | number; rx_util?: string | number; max_util?: string | number }
type MwEdge = {
  source: string
  target: string
  capacity?: number | null
  tx_throughput?: number | null
  rx_throughput?: number | null
  tx_util?: number | null
  rx_util?: number | null
  max_util?: number | null
  status?: string
  checkpoints?: MwCheckpoint[]
  history?: MwHistory[]
}
type MwRealtime = { nodes?: MwNode[]; edges?: MwEdge[]; meta?: Record<string, unknown> }

/** 模块级缓存：同一会话里多次点链路只打一次上游（2.8MB，值得缓存）。 */
let realtimeCache: Promise<MwRealtime> | null = null
function loadRealtime(): Promise<MwRealtime> {
  if (!realtimeCache) {
    realtimeCache = api
      .get<MwRealtime>('/mw-ume/realtime', { params: { top_n: 10 }, timeout: 180_000 })
      .then(({ data }) => (data && typeof data === 'object' ? data : {}))
      .catch((err) => {
        realtimeCache = null // 失败不缓存，下次点再试
        throw err
      })
  }
  return realtimeCache
}

/** 站名比较键：去首尾空白、内部空白压成一个、转小写。 */
const nameKey = (s: unknown): string =>
  String(s ?? '').trim().replace(/\s+/g, ' ').toLowerCase()

type Matched = {
  edge: MwEdge
  src?: MwNode
  tgt?: MwNode
  /** 命中方式：name=站名/站号，coord=坐标 2km 内最近节点 */
  how: 'name' | 'coord'
}

/**
 * 把 KML 链路匹配到实时 KPI 边。
 *
 * 优先按站名（KML 的 source/target 或 site_a/b 名称）找节点，再在边集合里找
 * 「两端都对上」的边（不分方向）；找不到再退化为端点坐标 2km 内的最近节点。
 * 匹配不上就返回 null —— 抽屉走「无 KPI 明细」分支。
 */
function matchEdge(link: TopologyLink, rt: MwRealtime): Matched | null {
  const nodes = Array.isArray(rt.nodes) ? rt.nodes : []
  const edges = Array.isArray(rt.edges) ? rt.edges : []
  if (!nodes.length || !edges.length) return null

  const byName = new Map<string, MwNode>()
  for (const n of nodes) {
    for (const k of [n.site, n.name, n.site_id]) {
      const key = nameKey(k)
      if (key && !byName.has(key)) byName.set(key, n)
    }
  }
  const srcKeys = [link.source, link.site_a_name, link.site_a_id, link.name?.split('-')[0]]
  const tgtKeys = [link.target, link.site_b_name, link.site_b_id, link.name?.split('-')[1]]
  const findNode = (keys: (string | undefined)[]): MwNode | undefined => {
    for (const k of keys) {
      const hit = byName.get(nameKey(k))
      if (hit) return hit
    }
    return undefined
  }
  const src = findNode(srcKeys)
  const tgt = findNode(tgtKeys)

  if (src && tgt) {
    const edge = edges.find(
      (e) => (e.source === src.id && e.target === tgt.id) || (e.source === tgt.id && e.target === src.id),
    )
    if (edge) return { edge, src, tgt, how: 'name' }
  }

  // 坐标兜底：端点 2km 内最近的节点（KML 站名与 UME 站点名常有大小写/缩写差异）
  const near = (lat: number, lon: number): MwNode | undefined => {
    let best: MwNode | undefined
    let bestKm = 2
    for (const n of nodes) {
      const nlat = Number(n.lat)
      const nlon = Number(n.lon)
      if (!Number.isFinite(nlat) || !Number.isFinite(nlon)) continue
      const km = haversineKm({ lat, lon }, { lat: nlat, lon: nlon })
      if (km < bestKm) { bestKm = km; best = n }
    }
    return best
  }
  const cs = near(link.lat_a, link.lng_a)
  const ct = near(link.lat_b, link.lng_b)
  if (cs && ct) {
    const edge = edges.find(
      (e) => (e.source === cs.id && e.target === ct.id) || (e.source === ct.id && e.target === cs.id),
    )
    if (edge) return { edge, src: cs, tgt: ct, how: 'coord' }
  }
  return null
}

const levelColor = (lv: UtilLevel): string => UTIL_LEVEL_COLORS[lv] || UTIL_LEVEL_COLORS.no_kpi

/** 分级 → 文案键。显式 switch 返回字面量键（动态拼接会被 check:i18n 拦下）。 */
function levelLabelKey(lv: UtilLevel): string {
  switch (lv) {
    case 'critical': return 'loc.mw.level.critical'
    case 'major': return 'loc.mw.level.major'
    case 'minor': return 'loc.mw.level.minor'
    case 'healthy': return 'loc.mw.level.healthy'
    default: return 'loc.mw.level.no_kpi'
  }
}
const fmtPct = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(Number(v)) ? '—' : `${Number(v).toFixed(2)}%`
const fmtNum = (v: number | null | undefined, digits = 2): string =>
  v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(digits)

export default function MwLinkDrawer({
  link, center, onClose,
}: {
  link: TopologyLink | null
  /** 位置点（用于显示「距该位置 X km」；不传则只显示链路本身） */
  center?: { lat: number; lon: number } | null
  onClose: () => void
}) {
  const { t } = useI18n()
  const { isDark, palette } = useTheme()
  const [rt, setRt] = useState<MwRealtime | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const open = !!link
  useEffect(() => {
    if (!open) return
    let alive = true
    setLoading(true)
    setError('')
    loadRealtime()
      .then((data) => { if (alive) setRt(data) })
      .catch((err: unknown) => {
        if (!alive) return
        setError(err instanceof Error ? err.message : String(err))
        setRt(null)
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [open, link?.name])

  const matched = useMemo(
    () => (link && rt ? matchEdge(link, rt) : null),
    [link, rt],
  )

  const km = useMemo(() => {
    if (!link || !center) return null
    return distanceToSegmentKm(center, { lat: link.lat_a, lon: link.lng_a }, { lat: link.lat_b, lon: link.lng_b })
  }, [link, center])

  const level: UtilLevel = link?.level ?? utilLevelOf(link?.util)
  const color = levelColor(level)
  const edge = matched?.edge
  const srcName = matched?.src?.site || matched?.src?.name || link?.source || link?.site_a_name || '—'
  const tgtName = matched?.tgt?.site || matched?.tgt?.name || link?.target || link?.site_b_name || '—'

  const trendOption = useMemo(() => {
    const hist = (edge?.history || []).filter((h) => h && h.date)
    if (!hist.length) return null
    const dates = hist.map((h) => String(h.date).slice(5, 16))
    const num = (v: unknown) => {
      const n = Number(v)
      return Number.isFinite(n) ? Number(n.toFixed(2)) : null
    }
    const tx = hist.map((h) => num(h.tx_util))
    const rx = hist.map((h) => num(h.rx_util))
    const max = hist.map((h) => num(h.max_util))
    const vals = [...tx, ...rx, ...max].filter((v): v is number => v != null)
    const lo = vals.length ? Math.max(0, Math.floor(Math.min(...vals) * 0.95)) : 0
    const hi = vals.length ? Math.max(lo + 1, Math.ceil(Math.max(...vals) * 1.05)) : 100
    return {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis' },
      legend: { data: ['TX', 'RX', 'Max'], textStyle: { color: palette.textMuted }, top: 0, right: 0 },
      grid: { left: 36, right: 10, top: 26, bottom: 24 },
      xAxis: {
        type: 'category', data: dates, boundaryGap: false,
        axisLabel: { color: palette.textMuted, fontSize: 10 },
        axisLine: { lineStyle: { color: palette.textDim } },
      },
      yAxis: {
        type: 'value', min: lo, max: hi,
        axisLabel: { formatter: '{value}%', color: palette.textMuted, fontSize: 10 },
        splitLine: { lineStyle: { color: isDark ? 'rgba(37,140,255,0.10)' : 'rgba(14,116,144,0.12)' } },
      },
      series: [
        { name: 'Max', type: 'line', data: max, showSymbol: false, itemStyle: { color },
          lineStyle: { width: 2, color } },
        { name: 'TX', type: 'line', data: tx, showSymbol: false, itemStyle: { color: palette.zteOrange },
          lineStyle: { width: 1.5, color: palette.zteOrange, type: 'dashed' } },
        { name: 'RX', type: 'line', data: rx, showSymbol: false, itemStyle: { color: palette.teal },
          lineStyle: { width: 1.5, color: palette.teal, type: 'dashed' } },
      ],
    }
  }, [edge, color, isDark, palette])

  const cps = edge?.checkpoints || []
  const head = cps[0] || {}

  return (
    <Drawer
      open={open}
      onClose={onClose}
      placement="right"
      width="min(95vw, 46rem)"
      title={link ? `${t('loc.mw.title')} · ${link.name || `${link.site_a_name}-${link.site_b_name}`}` : t('loc.mw.title')}
      destroyOnHidden
    >
      {!link ? null : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>
          {/* 状态 + 距离 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <Tag color={level === 'no_kpi' ? 'default' : 'red'} style={{ marginInlineEnd: 0 }}>
              <span style={{
                display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
                background: color, marginRight: 6,
              }} />
              {t(levelLabelKey(level))}
            </Tag>
            {km != null && <Text type="secondary">{t('loc.mw.distance', { d: fmtKm(km) })}</Text>}
            {matched && (
              <Text type="secondary">
                {t('loc.mw.matchedBy')}: {matched.how === 'name' ? t('loc.mw.matchName') : t('loc.mw.matchCoord')}
              </Text>
            )}
          </div>

          {loading && (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Spin />
              <div style={{ marginTop: 8 }}><Text type="secondary">{t('loc.mw.loading')}</Text></div>
            </div>
          )}
          {!!error && (
            <Alert type="warning" showIcon message={t('loc.mw.noKpi')} description={error} />
          )}

          {/* 总容量 */}
          <div>
            <div style={{ fontSize: 26, fontWeight: 700, color }}>
              {edge?.capacity == null
                ? '—'
                : edge.capacity >= 1000
                  ? `${(edge.capacity / 1000).toFixed(2)} Gbps`
                  : `${edge.capacity.toFixed(2)} Mbps`}
            </div>
            <div style={{ fontSize: 12, color: palette.textMuted }}>{t('loc.mw.capacity')}</div>
            {edge?.capacity == null && (
              <div style={{ fontSize: 12, color: palette.textMuted, marginTop: 4 }}>{t('loc.mw.noKpiHint')}</div>
            )}
          </div>

          {/* 两端 */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            border: `1px solid ${palette.textDim}33`, borderRadius: 8, padding: 10,
          }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, overflowWrap: 'anywhere' }}>{srcName}</div>
              {matched?.src?.site_id && (
                <div style={{ fontSize: 12, color: palette.textMuted }}>{t('loc.mw.siteId')}: {matched.src.site_id}</div>
              )}
            </div>
            <div style={{ color: palette.textMuted }}>→</div>
            <div style={{ flex: 1, minWidth: 0, textAlign: 'right' }}>
              <div style={{ fontWeight: 600, overflowWrap: 'anywhere' }}>{tgtName}</div>
              {matched?.tgt?.site_id && (
                <div style={{ fontSize: 12, color: palette.textMuted }}>{t('loc.mw.siteId')}: {matched.tgt.site_id}</div>
              )}
            </div>
          </div>

          {/* Node IP / 接口 */}
          {matched && (
            <>
              <Descriptions
                size="small" bordered column={1}
                items={[
                  { key: 'sip', label: t('loc.mw.nodeIp'), children: matched.src?.ip || '—' },
                  { key: 'tip', label: t('loc.mw.nodeIp'), children: matched.tgt?.ip || '—' },
                  { key: 'scp', label: t('loc.mw.interface'), children: head.cp || '—' },
                  { key: 'tcp', label: t('loc.mw.interface'), children: head.ncp || '—' },
                ]}
              />

              {/* TX / RX */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                {[
                  { k: 'tx' as const, label: t('loc.mw.tx'), thr: edge?.tx_throughput, u: edge?.tx_util },
                  { k: 'rx' as const, label: t('loc.mw.rx'), thr: edge?.rx_throughput, u: edge?.rx_util },
                ].map((box) => {
                  const lv = utilLevelOf(box.u)
                  return (
                    <div key={box.k} style={{
                      border: `1px solid ${palette.textDim}33`, borderRadius: 8, padding: 10,
                    }}>
                      <div style={{ fontSize: 12, color: palette.textMuted }}>{box.label}</div>
                      <div style={{ fontSize: 20, fontWeight: 700 }}>{fmtNum(box.thr)}</div>
                      <div style={{ fontSize: 12, color: palette.textMuted }}>{t('loc.mw.tput')}</div>
                      <div style={{ marginTop: 6, fontWeight: 700, color: levelColor(lv) }}>
                        {fmtPct(box.u)} <span style={{ fontWeight: 400, fontSize: 11 }}>{t('loc.mw.util')}</span>
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* 趋势 */}
              <div style={{ fontSize: 13, fontWeight: 600 }}>{t('loc.mw.trend')}</div>
              {trendOption ? (
                <Suspense fallback={<div style={{ height: 200 }} />}>
                  <ReactECharts option={trendOption} style={{ height: 200 }} notMerge />
                </Suspense>
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">{t('loc.mw.noTrend')}</Text>} />
              )}
              <div style={{ fontSize: 11, color: palette.textMuted }}>{t('loc.mw.trendNote')}</div>

              {/* 端口级明细 */}
              {cps.length > 1 && (
                <>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{t('loc.mw.checkpoints', { n: cps.length })}</div>
                  {cps.map((c, i) => (
                    <div key={`${c.cp}-${c.ncp}-${i}`} style={{
                      border: `1px solid ${palette.textDim}33`, borderRadius: 6,
                      padding: 8, fontSize: 12,
                    }}>
                      <div style={{ fontFamily: 'Consolas, monospace', color: palette.textMuted }}>
                        {c.cp || '—'} ↔ {c.ncp || '—'}
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>TX <b style={{ color: levelColor(utilLevelOf(Number(c.tx_u))) }}>{fmtPct(Number(c.tx_u))}</b></span>
                        <span>RX <b style={{ color: levelColor(utilLevelOf(Number(c.rx_u))) }}>{fmtPct(Number(c.rx_u))}</b></span>
                      </div>
                    </div>
                  ))}
                </>
              )}
            </>
          )}

          {/* 匹配不上：保留 GIS 侧结构信息，明确说「没有 KPI 明细」 */}
          {!matched && !loading && !error && (
            <Descriptions
              size="small" bordered column={1}
              items={[
                { key: 'a', label: t('loc.mw.endpointA'), children: `${link.site_a_name} (${link.lat_a.toFixed(5)}, ${link.lng_a.toFixed(5)})` },
                { key: 'b', label: t('loc.mw.endpointB'), children: `${link.site_b_name} (${link.lat_b.toFixed(5)}, ${link.lng_b.toFixed(5)})` },
                { key: 'u', label: t('loc.mw.util'), children: fmtPct(link.util) },
                { key: 'note', label: t('loc.mw.note'), children: t('loc.mw.noKpiHint') },
              ]}
            />
          )}

          {rt?.meta?.selected_date != null && (
            <Text type="secondary" style={{ fontSize: 11 }}>
              {t('loc.mw.slot', { slot: String(rt.meta.selected_date) })}
            </Text>
          )}
        </div>
      )}
    </Drawer>
  )
}
