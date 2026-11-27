/* ============================================================================
 * PRTG 链路健康页 · 拓扑 SVG（OverviewVisualSlot，P2）
 * ----------------------------------------------------------------------------
 * 只读：不拖动、不编辑。边仅来自 /api/network-topology/traffic。
 * 门户有完整编辑页（/uniplam/ → 拓扑视图），本页放外链入口。
 * ========================================================================== */

import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Empty, Segmented, Spin, Typography } from 'antd'

import { getNetworkTopologyTraffic } from '../../api'
import { useI18n } from '../../i18n'
import { WH } from '../../theme'
import { CardBase } from '../../components/cards'
import { ExplainIcon } from '../../components/MetricExplain'
import { explainStatusReason } from './prtgLogModels'
import {
  NA, PrtgLink, PrtgWindow, cleanSensorName, fmtNum, shortDeviceName,
} from './prtgModels'
import {
  TOPO_CANVAS, TopoLink, TopoTrafficPayload, findPageLink, layoutTopoNodes,
  mapWindowToTopoPeriod, mergeParallelEdges, paintEdge, sensorIdOf,
} from './prtgTopologyModels'

const { Text } = Typography
const STALE_SECONDS = 300
/** 门户拓扑视图无深链，打开门户首页后点「拓扑」；管理员可编辑布局/映射 */
const PORTAL_TOPOLOGY_HREF = '/uniplam/'

export interface PrtgTopologyViewProps {
  windowKey: PrtgWindow
  pageLinks: PrtgLink[]
  isDark: boolean
  highlightSensorId?: number | null
  onOpenDetail: (link: PrtgLink, at?: { x: number; y: number }) => void
  onSwitchToTrend: () => void
}

export default function PrtgTopologyView({
  windowKey, pageLinks, isDark, highlightSensorId, onOpenDetail, onSwitchToTrend,
}: PrtgTopologyViewProps) {
  const { t } = useI18n()
  const { period, fallbackFrom30d } = mapWindowToTopoPeriod(windowKey)
  const [data, setData] = useState<TopoTrafficPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const [hoverSensorId, setHoverSensorId] = useState<number | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    getNetworkTopologyTraffic({ period })
      .then(res => {
        if (!alive) return
        const payload = (res.data || null) as TopoTrafficPayload | null
        const nodes = Array.isArray(payload?.nodes) ? payload!.nodes! : []
        const links = Array.isArray(payload?.links) ? payload!.links! : []
        if (!nodes.length || !links.length) {
          setData(null)
          setError(t('prtg.topo.empty'))
          return
        }
        setData({ ...payload, nodes, links })
      })
      .catch((e: unknown) => {
        if (!alive) return
        setData(null)
        setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [period, t])

  const nodes = data?.nodes || []
  const links = data?.links || []
  const laid = useMemo(() => layoutTopoNodes(nodes, links), [nodes, links])
  const laidById = useMemo(() => new Map(laid.map(n => [n.id, n])), [laid])
  const merged = useMemo(() => mergeParallelEdges(links), [links])

  const activeHighlight = highlightSensorId ?? hoverSensorId

  const openBySensor = (topoLink: TopoLink, at?: { x: number; y: number }) => {
    const sid = sensorIdOf(topoLink)
    const page = findPageLink(pageLinks, sid)
    if (page) {
      onOpenDetail(page, at)
      return
    }
    // 拓扑有边但页面 links 暂未对齐时，用最小 PrtgLink 壳打开详情（仍走现有弹窗）
    if (sid != null) {
      onOpenDetail({
        sensor_id: sid,
        sensor_name: topoLink.target || topoLink.key || String(sid),
        device_name: topoLink.source || null,
        status: topoLink.status,
        in_mbps: topoLink.in_mbps,
        out_mbps: topoLink.out_mbps,
      }, at)
    }
  }

  const edgeTitle = (link: TopoLink): string => {
    const sid = sensorIdOf(link)
    const page = findPageLink(pageLinks, sid)
    const name = cleanSensorName(page?.sensor_name || link.target || link.key)
    const reason = page
      ? explainStatusReason(page.status, page.status_raw, page.last_message, page.unusual_reason, t)
      : t('prtg.cellExplain.status', { status: link.status || NA, raw: NA })
    const age = link.data_quality?.sample_age_seconds
    const stale = age != null && age > STALE_SECONDS
    const lines = [
      name,
      `In ${fmtNum(link.in_mbps)} / Out ${fmtNum(link.out_mbps)} Mbps`,
      reason,
    ]
    if (stale) lines.push(t('prtg.topo.staleHint', { n: age ?? NA }))
    if (link.utilization_pct != null && link.utilization_pct > 100) {
      lines.push(t('prtg.topo.overCapacity'))
    }
    return lines.join('\n')
  }

  const renderEdgePath = (
    a: { lx: number; ly: number },
    b: { lx: number; ly: number },
    bend: number,
  ) => {
    const mx = (a.lx + b.lx) / 2
    const my = (a.ly + b.ly) / 2 + bend
    return `M${a.lx},${a.ly} Q${mx},${my} ${b.lx},${b.ly}`
  }

  return (
    <CardBase
      title={<>{t('prtg.topo.title')} <ExplainIcon text={t('prtg.topo.explain')} /></>}
      extra={(
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {fallbackFrom30d && (
            <Text type="warning" style={{ fontSize: 12 }}>{t('prtg.topo.periodFallback7d')}</Text>
          )}
          <a href={PORTAL_TOPOLOGY_HREF} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
            {t('prtg.topo.portalEdit')}
          </a>
        </span>
      )}
      bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}
    >
      {loading && (
        <div style={{ padding: 24, textAlign: 'center' }}>
          <Spin tip={t('common.loading')}><div style={{ height: 40 }} /></Spin>
        </div>
      )}
      {!loading && error && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Alert type="error" showIcon message={t('prtg.topo.loadFailed')} description={error} />
          <Button size="small" onClick={onSwitchToTrend}>{t('prtg.topo.switchTrend')}</Button>
        </div>
      )}
      {!loading && !error && !data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-start' }}>
          <Empty description={t('prtg.topo.empty')} />
          <Button size="small" onClick={onSwitchToTrend}>{t('prtg.topo.switchTrend')}</Button>
        </div>
      )}
      {!loading && !error && data && (
        <>
          <svg
            viewBox={`0 0 ${TOPO_CANVAS.w} ${TOPO_CANVAS.h}`}
            role="img"
            aria-label={t('prtg.topo.title')}
            style={{ width: '100%', height: 'auto', display: 'block', background: 'transparent' }}
          >
            <title>{t('prtg.topo.title')}</title>
            {merged.map(edge => {
              const a = laidById.get(edge.source_node)
              const b = laidById.get(edge.target_node)
              if (!a || !b) return null
              const expanded = expandedKey === edge.key && edge.count > 1
              const drawLinks = expanded ? edge.links : [edge.worst]
              return drawLinks.map((link, idx) => {
                const paint = paintEdge(link, isDark)
                const sid = sensorIdOf(link)
                const hi = activeHighlight != null && sid === activeHighlight
                const bend = expanded
                  ? (idx - (drawLinks.length - 1) / 2) * 28
                  : (link.bend_offset ?? 0)
                const path = renderEdgePath(a, b, bend)
                const midX = (a.lx + b.lx) / 2
                const midY = (a.ly + b.ly) / 2 + bend * 0.5
                return (
                  <g key={`${edge.key}:${link.id}`}>
                    <path
                      d={path}
                      fill="none"
                      stroke={paint.color}
                      strokeWidth={hi ? paint.width + 2.5 : paint.width}
                      strokeDasharray={paint.dashed ? '6 4' : undefined}
                      strokeOpacity={hi ? 1 : 0.88}
                      style={{ cursor: 'pointer' }}
                      onMouseEnter={() => setHoverSensorId(sid)}
                      onMouseLeave={() => setHoverSensorId(null)}
                      onClick={e => {
                        e.stopPropagation()
                        if (!expanded && edge.count > 1) {
                          setExpandedKey(edge.key)
                          return
                        }
                        openBySensor(link, { x: e.clientX, y: e.clientY })
                      }}
                    >
                      <title>{edgeTitle(link)}</title>
                    </path>
                    {!expanded && edge.count > 1 && (
                      <g
                        style={{ cursor: 'pointer' }}
                        onClick={e => { e.stopPropagation(); setExpandedKey(edge.key) }}
                      >
                        <rect
                          x={midX - 14} y={midY - 10} width={28} height={18} rx={4}
                          fill={isDark ? '#0b1224' : '#fff'}
                          stroke={paint.color}
                        />
                        <text
                          x={midX} y={midY + 4} textAnchor="middle"
                          fontSize={11} fill={isDark ? '#f5f7ff' : '#16233b'}
                        >
                          ×{edge.count}
                        </text>
                      </g>
                    )}
                    {paint.overCapacity && (
                      <text
                        x={midX} y={midY - 12} textAnchor="middle"
                        fontSize={10} fill={paint.color} fontWeight={700}
                      >
                        {t('prtg.topo.overCapacity')}
                      </text>
                    )}
                  </g>
                )
              })
            })}
            {laid.map(n => {
              const label = shortDeviceName(n.name)
              const isDev = (n.type || '').toLowerCase() === 'device'
              const r = isDev ? 14 : 9
              return (
                <g key={n.id}>
                  <circle
                    cx={n.lx} cy={n.ly} r={r}
                    fill={isDev ? (isDark ? '#0d9488' : '#0f766e') : (isDark ? '#1e293b' : '#e2e8f0')}
                    stroke={isDark ? '#00d9ff' : '#0e7490'}
                    strokeWidth={isDev ? 2 : 1.2}
                  >
                    <title>{n.name}</title>
                  </circle>
                  <text
                    x={n.lx} y={n.ly + r + 14}
                    textAnchor="middle" fontSize={isDev ? 12 : 10}
                    fill={isDark ? '#aebbd4' : '#3f5468'}
                  >
                    {label.length > 18 ? `${label.slice(0, 17)}…` : label}
                  </text>
                </g>
              )
            })}
          </svg>
          {expandedKey && (
            <div style={{ marginTop: 8 }}>
              <Button size="small" onClick={() => setExpandedKey(null)}>
                {t('prtg.topo.collapseParallel')}
              </Button>
              <ParallelList
                edge={merged.find(m => m.key === expandedKey)}
                pageLinks={pageLinks}
                onPick={(link, at) => openBySensor(link, at)}
              />
            </div>
          )}
          <div style={{
            marginTop: 8, fontSize: 11, color: WH.textMuted,
            display: 'flex', flexWrap: 'wrap', gap: '8px 14px',
          }}>
            <span>{t('prtg.topo.legend.status')}</span>
            <span>{t('prtg.topo.legend.dash')}</span>
            <span>{t('prtg.topo.legend.width')}</span>
            <span>{t('prtg.topo.legend.multi')}</span>
            <span>{t('prtg.topo.legend.util')}</span>
          </div>
        </>
      )}
    </CardBase>
  )
}

function ParallelList({
  edge, pageLinks, onPick,
}: {
  edge?: ReturnType<typeof mergeParallelEdges>[number]
  pageLinks: PrtgLink[]
  onPick: (link: TopoLink, at?: { x: number; y: number }) => void
}) {
  const { t } = useI18n()
  if (!edge) return null
  return (
    <ul style={{ listStyle: 'none', margin: '8px 0 0', padding: 0 }}>
      {edge.links.map(link => {
        const sid = sensorIdOf(link)
        const page = findPageLink(pageLinks, sid)
        const name = cleanSensorName(page?.sensor_name || link.target || String(sid ?? NA))
        return (
          <li key={link.id}>
            <button
              type="button"
              onClick={e => onPick(link, { x: e.clientX, y: e.clientY })}
              style={{
                border: `1px solid ${WH.cardBorder}`, background: 'transparent',
                color: WH.text, width: '100%', textAlign: 'left',
                padding: '4px 8px', marginBottom: 4, borderRadius: 4, cursor: 'pointer',
              }}
            >
              {name}
              <span style={{ color: WH.textMuted, marginLeft: 8, fontSize: 12 }}>
                {link.status || NA} · In {fmtNum(link.in_mbps)} / Out {fmtNum(link.out_mbps)}
              </span>
            </button>
          </li>
        )
      })}
      <li style={{ fontSize: 12, color: WH.textMuted }}>{t('prtg.topo.parallelHint')}</li>
    </ul>
  )
}

/** 槽位顶栏：拓扑 / 趋势切换（供页面组装） */
export function PrtgVisualModeSwitch({
  mode, onChange,
}: {
  mode: 'topo' | 'trend'
  onChange: (m: 'topo' | 'trend') => void
}) {
  const { t } = useI18n()
  return (
    <Segmented
      className="wh-seg"
      size="small"
      value={mode}
      onChange={v => onChange(v as 'topo' | 'trend')}
      options={[
        { value: 'topo', label: t('prtg.slot.topo') },
        { value: 'trend', label: t('prtg.slot.trend') },
      ]}
    />
  )
}
