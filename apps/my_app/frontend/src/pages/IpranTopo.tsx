/* ============================================================================
 * UME 四层拓扑（?page=ipranTopo&layer=ipran|ipcore|metro|dwdm）
 * ============================================================================
 * 数据源：UME（ZTE NetNumen）四张逻辑拓扑层，端点序列相同、只有 lid 与口径不同。
 * 8 次上游调用由后端 services/ipran_topo 聚合成一次响应并**按层**缓存 120s
 * （层清单与口径见后端 layers.py、前端 ipranTopo/layerDefs.ts）。
 * 本页只负责「怎么画得好看、点得动」：
 *
 *   布局 3 种（力导向默认且带主归属聚类 / 地理 / 分层），原力导向留在 layoutForceClassic。
 *   着色 4 种：按层级（默认）/ 按在线状态 / 按最高告警级 / 按流量负载（Weathermap）。
 *     前三种里告警色**不是默认**：实测 80% 网元都带紧急告警，按告警级填色会全红、
 *     失去区分度（后端 warnings 也会说明）。第四种是唯一让连线颜色表达业务量的模式，
 *     只在用户主动切过去时**额外取一次流量**（`?traffic=1`），不切就不付这份钱；
 *     端口流量只有 KPI 采集覆盖的那几个网元有，其余显示 N/A（不是 0%）。
 *   告警只做节点右上角小红点；带宽只做线宽；断链红色虚线。颜色全部服务于故障态。
 *
 * 交互：点节点聚焦（其余变暗、只显示一跳邻居），详情从右侧滑出；
 * 右栏（约 25%，与微波实时页同一套分栏）从上到下是统计卡、紧急告警榜，
 * 两者同宽；左栏画布吃掉工具条之下剩余的全部高度。
 * 点边看物理链路明细；
 * 统计卡点开是「构成这个数的那些行」（TopoDrillModal），与卡上数字同一份数据。
 * 切页签会清空旧层数据与聚焦态，并用请求序号丢弃「切走后迟到的旧层响应」；
 * 自动刷新：已登记 AR_INPLACE_REFRESH_PAGES，监听 myapp-refresh 只静默重取当前层。
 * ========================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent } from 'react'
import { Alert, Button, Drawer, Empty, Popconfirm, Segmented, Space, Spin, Switch } from 'antd'
import { ArrowLeftOutlined, EditOutlined, HeatMapOutlined, ReloadOutlined, UndoOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'

import { useI18n } from '../i18n'
import { WH } from '../theme'
import { CardBase } from '../components/cards'
import { ExplainIcon, ExplainPopover } from '../components/MetricExplain'
import CalcChainNote, { type CalcChainSection } from '../components/CalcChainNote'
import { exportReport } from '../utils/excelReport'
import { formatDateTime } from '../datetime'
import { getIpranTopoGraph, getIpranTopoLayout, resetIpranTopoLayout, saveIpranTopoLayout } from '../api'
import TopoCanvas from './ipranTopo/TopoCanvas'
import WeathermapCanvas from './ipranTopo/WeathermapCanvas'
import TopoDrillModal, { type DrillKind } from './ipranTopo/TopoDrillModal'
import TopoGeoMap from './ipranTopo/TopoGeoMap'
import TopologyControls, { type StatusFilter, type TierFilter } from './ipranTopo/TopologyControls'
import TopologyDetails from './ipranTopo/TopologyDetails'
import TopologyLegend from './ipranTopo/TopologyLegend'
import TopologyMetrics from './ipranTopo/TopologyMetrics'
import { buildIndex } from './ipranTopo/layouts'
import { cachedLayout } from './ipranTopo/layoutCache'
import { analyzeGraph } from './ipranTopo/faultAnalysis'
import { LAYER_KEYS, LAYER_LABEL_KEY, calcSectionKeys, explainKeys, isLayerKey } from './ipranTopo/layerDefs'
import { TRAFFIC_BINS, TRAFFIC_NA_COLOR } from './ipranTopo/trafficScale'
import type { ColorMode, LayerKey, LayoutKey, Tier, TopoNode, TopoResp } from './ipranTopo/types'

type Drill = { kind: DrillKind; anchor: { x: number; y: number } }
type ViewCmd = { seq: number; kind: 'in' | 'out' | 'fit' }

/** 门户 GIS Map 的 IP 页签（`?maponly=1`）只留网络拓扑画布。
 *  与 Person 页的 maponly 同一约定：embed 只藏外壳，这个参数才藏页面自己的
 *  工具条 / KPI / 告警榜。Open Link 会剥掉它，新窗口仍是完整页。 */
function readMapOnlyMode(): boolean {
  try {
    const sp = new URLSearchParams(window.location.search)
    return sp.get('maponly') === '1' || sp.get('maponly') === 'true'
  } catch { return false }
}

export default function IpranTopo() {
  const { t } = useI18n()

  /* 层：URL 的 &layer= 可深链到单层，非法值回落到 ipran（与后端白名单一致） */
  const [mapOnly] = useState(readMapOnlyMode)
  /* 第二个视图：Weathermap。地址栏用 &view=weathermap 深链，默认仍是拓扑 */
  const [view, setView] = useState<'topo' | 'weathermap'>(() => {
    try {
      return new URLSearchParams(window.location.search).get('view') === 'weathermap' ? 'weathermap' : 'topo'
    } catch { return 'topo' }
  })
  const [layer, setLayer] = useState<LayerKey>(() => {
    const v = new URLSearchParams(window.location.search).get('layer')
    return isLayerKey(v) ? v : 'ipran'
  })
  const [resp, setResp] = useState<TopoResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [layoutKey, setLayoutKey] = useState<LayoutKey>('force')
  /* Weathermap 视图单独记布局：在那边切分层/地理不该改掉拓扑视图的选择 */
  const [wmLayout, setWmLayout] = useState<LayoutKey>('force')
  /* 默认画全量（100 个点）保持 Weathermap 的密度；关掉只看骨干 22 个点 */
  const [wmAccess, setWmAccess] = useState(true)
  /* 手工布局：nodeId -> [x, y]（760×540 逻辑画布）。编辑态拖动、松手保存 */
  const [wmPos, setWmPos] = useState<Record<string, [number, number]>>({})
  const wmPosRef = useRef(wmPos)
  const [wmEdit, setWmEdit] = useState(false)
  /* null = 还没问到；true/false = 服务端是否允许写（本机 / 持令牌） */
  const [wmEditable, setWmEditable] = useState<boolean | null>(null)
  /* loading -> 正在问接口；ready -> 接口可用；missing -> 未部署/未重启，退回本机保存 */
  const [wmServer, setWmServer] = useState<'loading' | 'ready' | 'missing'>('loading')
  const [wmSavedAt, setWmSavedAt] = useState<string | null>(null)
  const [wmSaveMsg, setWmSaveMsg] = useState<'idle' | 'saving' | 'saved' | 'local' | 'forbidden' | 'failed'>('idle')
  const [colorMode, setColorMode] = useState<ColorMode>('tier')
  const [showAllLabels, setShowAllLabels] = useState(false)
  const [foldParallel, setFoldParallel] = useState(true)
  const [focus, setFocus] = useState(-1)
  const [selectedEdge, setSelectedEdge] = useState(-1)
  const [query, setQuery] = useState('')
  const [tierFilter, setTierFilter] = useState<TierFilter>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [viewCmd, setViewCmd] = useState<ViewCmd | null>(null)
  const [drill, setDrill] = useState<Drill | null>(null)
  // 快速连切页签时先发的请求可能后返回，只认最后一次请求的响应
  const reqSeq = useRef(0)
  /* 是否要端口流量：用 ref 而不是 state，避免切回别的着色时又白取一次 UME。
     切到流量模式时由下面的 effect 触发一次带 traffic=1 的请求。 */
  const wantTraffic = useRef(false)

  /* 取数：silent 供原地自动刷新（不清 loading、失败保留旧数据），force 绕过后端 120s 缓存 */
  const load = useCallback(async (opts?: { silent?: boolean; force?: boolean }) => {
    const seq = ++reqSeq.current
    const silent = !!opts?.silent
    if (!silent) { setLoading(true); setError('') }
    try {
      const r = await getIpranTopoGraph(layer, opts?.force ? 1 : 0, wantTraffic.current ? 1 : 0)
      if (seq !== reqSeq.current) return
      setResp(r.data as TopoResp)
      if (!silent) setError('')
    } catch (e: any) {
      if (seq !== reqSeq.current) return
      setError(e?.message || String(e))
    } finally {
      if (seq === reqSeq.current && !silent) setLoading(false)
    }
  }, [layer])

  useEffect(() => { void load() }, [load])
  /* 着色切到「流量负载」时才去取端口速率（多读 6 张 KPI 表）；已经有这份数据就不重复取。
     切走时**不重取** —— 服务端两份响应各自缓存，回来时还能直接命中。 */
  useEffect(() => {
    const on = colorMode === 'traffic'
    if (on === wantTraffic.current) return
    wantTraffic.current = on
    if (on && !resp?.meta?.traffic) void load()
  }, [colorMode, resp, load])
  /* Weathermap 视图就是流量视图：进来自动切 traffic（并触发一次 traffic=1 取数） */
  useEffect(() => {
    if (view === 'weathermap' && colorMode !== 'traffic') setColorMode('traffic')
  }, [view, colorMode])
  useEffect(() => {
    const onInplaceRefresh = () => { void load({ silent: true }) }
    document.addEventListener('myapp-refresh', onInplaceRefresh)
    return () => document.removeEventListener('myapp-refresh', onInplaceRefresh)
  }, [load])
  /* 地址栏保留当前层与视图，便于分享单层链接；replaceState 不产生多余历史，App.tsx 的
     page 同步用 set('page')，不会丢掉 layer / view 参数 */
  useEffect(() => {
    const url = new URL(window.location.href)
    url.searchParams.set('layer', layer)
    url.searchParams.set('view', view)
    window.history.replaceState(window.history.state, '', url)
  }, [layer, view])

  const changeLayer = (k: LayerKey) => {
    if (k === layer) return
    // 旧层数据/聚焦/下钻不能带到新层：先清空，load 因 layer 变化会自动取新层
    setLayer(k)
    setResp(null)
    setError('')
    setLoading(true)
    setFocus(-1)
    setSelectedEdge(-1)
    setDrill(null)
  }

  /* 进 Weathermap 视图时读该层保存的手工布局。接口还没部署/没重启时 404，
     退回本机 localStorage —— 编辑不会因此不可用，只是暂时只有这台机器看得到。 */
  useEffect(() => {
    if (view !== 'weathermap') return
    let alive = true
    setWmServer('loading')
    setWmSaveMsg('idle')
    getIpranTopoLayout(layer).then((r) => {
      if (!alive) return
      const d = r.data || {}
      const nodes = (d.nodes || {}) as Record<string, [number, number]>
      wmPosRef.current = nodes
      setWmPos(nodes)
      setWmSavedAt(d.updated_at || null)
      setWmEditable(!!d.editable)
      setWmServer('ready')
    }).catch(() => {
      if (!alive) return
      setWmServer('missing')
      setWmEditable(true)
      try {
        const raw = localStorage.getItem(`myapp_wm_layout_${layer}`)
        const d = raw ? JSON.parse(raw) : null
        const nodes = (d?.nodes || {}) as Record<string, [number, number]>
        wmPosRef.current = nodes
        setWmPos(nodes)
        setWmSavedAt(d?.updated_at || null)
      } catch {
        wmPosRef.current = {}
        setWmPos({})
      }
    })
    return () => { alive = false }
  }, [view, layer])

  const moveWmNode = useCallback((id: string, x: number, y: number) => {
    const nx: [number, number] = [Math.round(x * 10) / 10, Math.round(y * 10) / 10]
    /* 先同步更新 ref：松手时 onCommit 紧接着保存，不能等 React 的 setState 落地 */
    const next = { ...wmPosRef.current, [id]: nx }
    wmPosRef.current = next
    setWmPos(next)
  }, [])

  const saveWm = useCallback(async () => {
    const nodes = wmPosRef.current
    const at = new Date().toISOString()
    try {
      localStorage.setItem(`myapp_wm_layout_${layer}`, JSON.stringify({ nodes, updated_at: at }))
    } catch { /* 本机容量满时忽略，服务端保存仍然有效 */ }
    if (wmServer !== 'ready') {
      setWmSavedAt(at)
      setWmSaveMsg('local')
      return
    }
    setWmSaveMsg('saving')
    try {
      const r = await saveIpranTopoLayout(layer, nodes)
      setWmSavedAt(r.data?.updated_at || at)
      setWmSaveMsg('saved')
    } catch (e: any) {
      setWmSaveMsg(e?.response?.status === 403 ? 'forbidden' : 'failed')
    }
  }, [layer, wmServer])

  const resetWm = useCallback(async () => {
    wmPosRef.current = {}
    setWmPos({})
    setWmSavedAt(null)
    setWmSaveMsg('idle')
    try { localStorage.removeItem(`myapp_wm_layout_${layer}`) } catch { /* ignore */ }
    if (wmServer === 'ready') {
      try { await resetIpranTopoLayout(layer) } catch { setWmSaveMsg('failed') }
    }
  }, [layer, wmServer])
  const explain = explainKeys(layer)

  const nodes = useMemo(() => resp?.nodes ?? [], [resp])
  const edges = useMemo(() => resp?.edges ?? [], [resp])
  const idx = useMemo(() => buildIndex(nodes, edges), [nodes, edges])
  const layout = useMemo(
    () => cachedLayout(layoutKey, nodes, idx, edges),
    [layoutKey, nodes, idx, edges],
  )
  const wmLayoutData = useMemo(
    () => cachedLayout(wmLayout, nodes, idx, edges),
    [wmLayout, nodes, idx, edges],
  )
  const focusSafe = focus >= 0 && focus < nodes.length ? focus : -1

  /* ---- 统计（卡片与下钻弹窗取同一份数组，口径不会对不上） ---- */
  const meta = resp?.meta
  const ne = meta?.ne ?? 0
  const ext = meta?.ext ?? 0
  const rawLinks = meta?.rawLinks ?? 0
  const folded = meta?.foldedLinks ?? 0
  const critNe = meta?.critNe ?? 0
  const critPct = ne ? Math.round((critNe * 100) / ne) : 0
  const downNe = useMemo(() => nodes.filter((v) => v.tier > 0 && !v.up).length, [nodes])

  const footMap = (w: string): string | null => {
    const tm = meta?.traffic
    if (w.startsWith('traffic_no_data')) return t('ipranTopo.warn.trafficNoData')
    if (w.startsWith('traffic_unmatched')) {
      return t('ipranTopo.warn.trafficUnmatched', { n: tm?.totalEdges ?? 0 })
    }
    if (w.startsWith('traffic_partial')) {
      const m = tm?.matchedEdges ?? 0
      const n = tm?.totalEdges ?? 0
      return t('ipranTopo.warn.trafficPartial', { m, n, pct: n ? Math.round((m * 100) / n) : 0 })
    }
    if (w.startsWith('traffic_no_bandwidth')) {
      return t('ipranTopo.warn.trafficNoBandwidth', { n: tm?.noBandwidthPorts ?? 0 })
    }
    return null
  }

  /* 告警按页面已知的口径就地重建成中英双语；后端未知前缀（如 load_failed）原样显示，不吞。
     取数降级（stale）留在工具条下，用户正在看的不是本次结果。
     告警饱和 / 缺坐标 / 近似匹配是说明，不是本次取数失败，放到页底，避免把画布顶下去。 */
  const { topWarns, footWarns } = useMemo(() => {
    const top: string[] = []
    const foot: string[] = []
    if (!meta) return { topWarns: top, footWarns: foot }
    if (meta.stale) top.push(t('ipranTopo.warn.stale'))
    if (ne && critNe / ne > 0.5) {
      foot.push(t('ipranTopo.warn.alarmSaturated', { crit: critNe, ne, pct: critPct }))
    }
    const miss = meta.geo?.miss ?? 0
    if (miss) foot.push(t('ipranTopo.warn.geoMiss', { n: miss }))
    const fuzzy = meta.geo?.fuzzy ?? 0
    if (fuzzy) foot.push(t('ipranTopo.warn.geoFuzzy', { n: fuzzy }))
    for (const w of meta.warnings || []) {
      if (/^(alarm_saturated|geo_miss|geo_fuzzy|upstream_unreachable):/.test(w)) continue
      const mapped = footMap(w)
      if (mapped) { foot.push(mapped); continue }
      foot.push(w)
    }
    return { topWarns: top, footWarns: foot }
  }, [meta, ne, critNe, critPct, t])

  const tierName = (tier: Tier) => {
    switch (tier) {
      case 0: return t('ipranTopo.tier.0')
      case 1: return t('ipranTopo.tier.1')
      case 2: return t('ipranTopo.tier.2')
      default: return t('ipranTopo.tier.3')
    }
  }

  const filtering = tierFilter !== 'all' || statusFilter !== 'all' || query.trim().length > 0
  const dimmed = useMemo(() => {
    if (!filtering) return null
    const q = query.trim().toLowerCase()
    return nodes.map((v) => {
      if (tierFilter !== 'all' && v.tier !== tierFilter) return true
      if (statusFilter === 'up' && !v.up) return true
      if (statusFilter === 'down' && !!v.up) return true
      if (statusFilter === 'alarm' && !(v.crit || v.major || v.minor || v.warn)) return true
      if (q && !`${v.name} ${v.site} ${v.ip}`.toLowerCase().includes(q)) return true
      return false
    })
  }, [filtering, nodes, tierFilter, statusFilter, query])

  const fault = useMemo(() => analyzeGraph(idx, nodes, edges), [idx, nodes, edges])
  const showImpact = focusSafe >= 0 && (nodes[focusSafe]?.up === 0
    || fault.candidates.some((c) => c.index === focusSafe))
  const impacted = useMemo(() => {
    if (!showImpact || focusSafe < 0) return null
    const downStream = new Set<number>()
    const tier = nodes[focusSafe].tier
    const seen = new Set<number>([focusSafe])
    const queue = [focusSafe]
    const isolated = new Set(fault.isolated)
    for (let head = 0; head < queue.length; head++) {
      for (const a of idx.adj[queue[head]] || []) {
        const nb = nodes[a.n]
        if (!nb || nb.tier <= tier || seen.has(a.n)) continue
        seen.add(a.n)
        if (isolated.has(a.n) || nb.up === 0) {
          downStream.add(a.n)
          queue.push(a.n)
        }
      }
    }
    return downStream
  }, [showImpact, focusSafe, nodes, idx, fault])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const tag = (e.target as HTMLElement | null)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      setFocus(-1)
      setSelectedEdge(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const locate = () => {
    const q = query.trim().toLowerCase()
    if (!q) return
    const hit = nodes.findIndex((v) => `${v.name} ${v.site} ${v.ip}`.toLowerCase().includes(q))
    if (hit >= 0) { setFocus(hit); setSelectedEdge(-1) }
  }

  const bumpView = (kind: ViewCmd['kind']) => setViewCmd((prev) => ({ seq: (prev?.seq ?? 0) + 1, kind }))

  /* ---- 告警 Top 10：点网元名聚焦到画布 ---- */
  const topRows = useMemo(
    () => nodes
      .filter((v) => v.tier > 0 && (v.crit > 0 || v.major > 0))
      .sort((a, b) => b.crit - a.crit || b.major - a.major)
      .slice(0, 10),
    [nodes],
  )

  /* 右栏大约 340px，七列表格会把「网元」列压成 0 宽，名字逐字竖排，一行能到 600px。
     这里跟微波实时页的风险榜一样：固定 3rem 一行，名字省略，紧急数贴右。
     层级 / 站点 / 其余告警级放在第二行，悬停数字仍是原来的分项说明。 */

  const inLayer = useMemo(() => nodes.filter((v) => v.tier > 0).length, [nodes])

  /* ---- 导出：网元 + 链路两张 sheet（统一走 excelReport） ---- */
  const doExport = useCallback(() => {
    if (!resp) return
    // 导出标题/文件名带层名：四层表格落到磁盘后还能分清是哪一层
    const exportTitle = `${t('ipranTopo.export.title')} · ${t(LAYER_LABEL_KEY[layer])}`
    const nodeRows = resp.nodes.map((v) => [
      v.name, tierName(v.tier), v.site, v.model, v.ip,
      v.up ? t('ipranTopo.status.up') : t('ipranTopo.status.down'),
      v.crit, v.major, v.minor, v.warn, v.deg,
      v.lat ?? '', v.lon ?? '', v.geo,
    ])
    const linkRows = resp.edges.map((e) => [
      resp.nodes[idx.byId[e.s]]?.name ?? e.s,
      resp.nodes[idx.byId[e.t]]?.name ?? e.t,
      e.n, e.gb ?? '', e.down, e.crit, e.major, e.ports.join(' | '),
      e.rxUtil ?? '', e.txUtil ?? '',
    ])
    void exportReport([
      {
        name: t('ipranTopo.export.nodes'), title: exportTitle,
        header: [
          t('ipranTopo.col.name'), t('ipranTopo.col.tier'), t('ipranTopo.col.site'), t('ipranTopo.col.model'),
          t('ipranTopo.col.ip'), t('ipranTopo.col.status'), t('ipranTopo.col.crit'), t('ipranTopo.col.major'),
          t('ipranTopo.col.minor'), t('ipranTopo.col.warn'), t('ipranTopo.col.deg'),
          t('ipranTopo.col.lat'), t('ipranTopo.col.lon'), t('ipranTopo.col.geo'),
        ],
        rows: nodeRows, numericCols: [6, 7, 8, 9, 10],
      },
      {
        name: t('ipranTopo.export.links'), title: exportTitle,
        header: [
          t('ipranTopo.col.peerA'), t('ipranTopo.col.peerB'), t('ipranTopo.col.physLinks'),
          t('ipranTopo.col.gb'), t('ipranTopo.col.downLinks'), t('ipranTopo.col.crit'),
          t('ipranTopo.col.major'), t('ipranTopo.col.ports'),
          t('ipranTopo.col.rxUtil'), t('ipranTopo.col.txUtil'),
        ],
        rows: linkRows, numericCols: [2, 3, 4, 5, 6, 8, 9], autoWidth: false,
        columnWidths: [26, 26, 10, 10, 10, 9, 9, 52, 10, 10],
      },
    ], `UME_Topo_${layer}_${dayjs().format('YYYYMMDD_HHmmss')}.xlsx`)
  }, [resp, idx, t, layer])

  /* 口径说明按层切换：ipran 用实测版，其余三层用通用版（不套用 93/167 这类实测数字）。
     切到流量模式且本次响应带了流量数据时，再补一节流量口径（含本次实际覆盖数）。 */
  const calcSections = useMemo<CalcChainSection[]>(() => {
    const out = calcSectionKeys(layer).map((s) => ({
      title: t(s.title), lines: s.lines.map((k) => t(k)),
    }))
    const tm = colorMode === 'traffic' ? meta?.traffic : undefined
    if (tm) {
      const m = tm.matchedEdges
      const n = tm.totalEdges
      out.push({
        title: t('ipranTopo.calc.traffic.title'),
        lines: [
          t('ipranTopo.calc.traffic.l1', {
            tables: tm.tables.length,
            at: tm.sampledAt ? formatDateTime(tm.sampledAt) : t('ipranTopo.detail.na'),
          }),
          t('ipranTopo.calc.traffic.l2'),
          t('ipranTopo.calc.traffic.l3'),
          t('ipranTopo.calc.traffic.l4'),
          t('ipranTopo.calc.traffic.l5', { m, n, pct: n ? Math.round((m * 100) / n) : 0 }),
        ],
      })
    }
    return out
  }, [layer, t, meta, colorMode])

  const openDrill = (kind: DrillKind) => (e: ReactMouseEvent<HTMLDivElement>) =>
    setDrill({ kind, anchor: { x: e.clientX, y: e.clientY } })

  /* 门户 IP 页签：只留网络拓扑。工具条、KPI、口径、告警榜都不画。
     缩放钮留在画布上，因为门户默认锁住滚轮（跟 Person 的 Leaflet +/- 一样）。
     点节点/链路仍开右侧详情，那是画布自己的交互，不是页面上的另一块。 */
  if (mapOnly) {
    const zoomBtn: CSSProperties = {
      width: 30, height: 30, padding: 0, cursor: 'pointer',
      border: `1px solid ${WH.cardBorder}`, borderRadius: 4,
      background: WH.cardBg, color: WH.text, fontSize: 16, lineHeight: '28px',
    }
    return (
      <div style={{ flex: '1 1 auto', height: '100%', minHeight: 0, position: 'relative', overflow: 'hidden' }}>
        {error && <Alert type="error" showIcon message={error} style={{ margin: 8 }} />}
        {meta?.stale && !error && (
          <Alert type="warning" showIcon message={t('ipranTopo.warn.stale')} style={{ margin: 8 }} />
        )}
        {loading && !resp ? (
          <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin /></div>
        ) : nodes.length === 0 ? (
          <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Empty description={t('ipranTopo.empty')} />
          </div>
        ) : (
          <TopoCanvas
            fill
            nodes={nodes} edges={edges} idx={idx} layout={layout}
            colorMode={colorMode} focus={focusSafe}
            showAllLabels={showAllLabels} foldParallel={foldParallel}
            dimmed={dimmed} impacted={impacted} selectedEdge={selectedEdge}
            viewCmd={viewCmd} resetKey={`${layer}:${layoutKey}`}
            markGeo={layoutKey === 'geo'}
            trafficAt={meta?.traffic?.sampledAt ? formatDateTime(meta.traffic.sampledAt) : null}
            onToggleFocus={(i) => { setFocus((prev) => (prev === i ? -1 : i)); setSelectedEdge(-1) }}
            onSelectEdge={(i) => { setSelectedEdge(i); setFocus(-1) }}
            onBackgroundClick={() => { setFocus(-1); setSelectedEdge(-1) }}
          />
        )}
        <div style={{ position: 'absolute', left: 12, right: 56, bottom: 8, zIndex: 2, pointerEvents: 'none' }}>
          <TopologyLegend colorMode={colorMode} tierCounts={meta?.tierCounts} />
        </div>
        <div style={{ position: 'absolute', top: 12, right: 12, display: 'flex', flexDirection: 'column', gap: 4, zIndex: 2 }}>
          <button type="button" style={zoomBtn} title={t('ipranTopo.btn.zoomIn')} onClick={() => bumpView('in')}>+</button>
          <button type="button" style={zoomBtn} title={t('ipranTopo.btn.zoomOut')} onClick={() => bumpView('out')}>−</button>
          <button type="button" style={zoomBtn} title={t('ipranTopo.btn.fit')} onClick={() => bumpView('fit')}>⛶</button>
        </div>
        <Drawer
          placement="right"
          width="min(95vw, 45rem)"
          open={focusSafe >= 0 || selectedEdge >= 0}
          onClose={() => { setFocus(-1); setSelectedEdge(-1) }}
          title={focusSafe >= 0
            ? t('ipranTopo.focus.title', { name: nodes[focusSafe]?.name ?? '' })
            : t('ipranTopo.detail.linkTitle')}
          destroyOnClose
        >
          {(focusSafe >= 0 || selectedEdge >= 0) && (
            <TopologyDetails
              nodes={nodes} edges={edges} idx={idx} fault={fault}
              focus={focusSafe} selectedEdge={selectedEdge}
              onFocus={(i) => { setFocus(i); setSelectedEdge(-1) }}
            />
          )}
        </Drawer>
      </div>
    )
  }

  /* ---------- Weathermap 视图：整屏一张流量图，不摆统计卡与告警榜 ---------- */
  if (view === 'weathermap') {
    const tm = meta?.traffic
    return (
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{
          flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
          background: WH.cardBg, border: `1px solid ${WH.cardBorder}`, borderRadius: 8, padding: '6px 10px',
        }}>
          <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => setView('topo')}>
            {t('ipranTopo.wm.back')}
          </Button>
          <Segmented className="wh-seg" size="small" value={layer}
            onChange={(v) => changeLayer(v as LayerKey)}
            options={LAYER_KEYS.map((k) => ({ label: t(LAYER_LABEL_KEY[k]), value: k }))} />
          <Segmented className="wh-seg" size="small" value={wmLayout}
            onChange={(v) => setWmLayout(v as LayoutKey)}
            options={[
              { label: t('ipranTopo.layout.force'), value: 'force' },
              { label: t('ipranTopo.layout.tier'), value: 'tier' },
              { label: t('ipranTopo.layout.geo'), value: 'geo' },
            ]} />
          <Switch size="small" checked={showAllLabels} onChange={setShowAllLabels}
            checkedChildren={t('ipranTopo.opt.allLabels')} unCheckedChildren={t('ipranTopo.opt.allLabels')} />
          <Switch size="small" checked={wmAccess} onChange={setWmAccess}
            checkedChildren={t('ipranTopo.wm.access')} unCheckedChildren={t('ipranTopo.wm.access')} />
          <Button size="small" icon={<ReloadOutlined />} loading={loading}
            onClick={() => void load({ force: true })}>{t('ipranTopo.btn.refresh')}</Button>
          <Button size="small" type={wmEdit ? 'primary' : 'default'} icon={<EditOutlined />}
            disabled={wmEditable !== true}
            onClick={() => setWmEdit((v) => !v)}>
            {t('ipranTopo.wm.edit')}
          </Button>
          {(wmEdit || Object.keys(wmPos).length > 0) && (
            <Popconfirm
              title={t('ipranTopo.wm.resetConfirm')}
              okText={t('ipranTopo.wm.reset')}
              cancelText={t('common.cancel')}
              onConfirm={() => void resetWm()}
            >
              <Button size="small" icon={<UndoOutlined />} disabled={wmEditable !== true || !Object.keys(wmPos).length}>
                {t('ipranTopo.wm.reset')}
              </Button>
            </Popconfirm>
          )}
          {wmEdit && (
            <span style={{
              fontSize: 12,
              color: wmSaveMsg === 'failed' || wmSaveMsg === 'forbidden' ? WH.danger : WH.textDim,
            }}>
              {wmSaveMsg === 'saving' ? t('ipranTopo.wm.saving')
                : wmSaveMsg === 'local' ? t('ipranTopo.wm.localOnly')
                  : wmSaveMsg === 'forbidden' ? t('ipranTopo.wm.forbidden')
                    : wmSaveMsg === 'failed' ? t('ipranTopo.wm.saveFailed')
                      : wmSavedAt ? t('ipranTopo.wm.saved', { at: formatDateTime(wmSavedAt) })
                        : t('ipranTopo.wm.editHint')}
            </span>
          )}
          <span style={{ marginLeft: 'auto', color: WH.textDim, fontSize: 12 }}>{t('ipranTopo.wm.hint')}</span>
        </div>

        {error && <Alert type="error" showIcon message={error} />}
        {topWarns.length > 0 && (
          <Alert type="warning" showIcon message={<div>{topWarns.map((w, i) => <div key={i}>{w}</div>)}</div>} />
        )}

        <div style={{
          flex: '1 1 0%', minHeight: 320, position: 'relative', overflow: 'hidden',
          background: WH.cardBg, border: `1px solid ${WH.cardBorder}`, borderRadius: 8,
        }}>
          {loading && !resp ? (
            <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin /></div>
          ) : nodes.length === 0 ? (
            <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Empty description={t('ipranTopo.empty')} />
            </div>
          ) : (
            <>
              <WeathermapCanvas
                nodes={nodes} edges={edges} idx={idx} layout={wmLayoutData}
                showAllLabels={showAllLabels} includeAccess={wmAccess} focus={focusSafe} selectedEdge={selectedEdge}
                positions={wmPos} editable={wmEdit}
                onMove={moveWmNode} onCommit={() => void saveWm()}
                onFocus={(i) => { setFocus((prev) => (prev === i ? -1 : i)); setSelectedEdge(-1) }}
                onSelectEdge={(i) => { setSelectedEdge(i); setFocus(-1) }}
                onBackground={() => { setFocus(-1); setSelectedEdge(-1) }}
              />
              {/* 图例与采样时刻压在画布上，不占地图高度；竖排更接近 NMS 的图例 */}
              <div style={{ position: 'absolute', left: 10, bottom: 10, pointerEvents: 'none' }}>
                <div style={{
                  background: WH.cardBg, border: `1px solid ${WH.cardBorder}`, borderRadius: 6,
                  padding: '6px 10px 7px', fontSize: 11, minWidth: 118,
                }}>
                  <div style={{ fontWeight: 600, marginBottom: 3 }}>{t('ipranTopo.legend.traffic')}</div>
                  {TRAFFIC_BINS.map((b) => (
                    <div key={b.label} style={{ display: 'flex', alignItems: 'center', gap: 6, lineHeight: '15px' }}>
                      <span style={{
                        width: 14, height: 9, background: b.color, display: 'inline-block',
                        border: `1px solid ${WH.cardBorder}`,
                      }} />
                      <span style={{ color: WH.textMuted }}>{b.label}</span>
                    </div>
                  ))}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, lineHeight: '15px', marginTop: 2 }}>
                    <span style={{ width: 14, borderTop: `2px dashed ${TRAFFIC_NA_COLOR}`, display: 'inline-block' }} />
                    <span style={{ color: WH.textMuted }}>{t('ipranTopo.legend.trafficNa')}</span>
                  </div>
                </div>
              </div>
              <div style={{
                position: 'absolute', right: 10, top: 10, textAlign: 'right', fontSize: 11,
                color: WH.textMuted, background: WH.cardBg, border: `1px solid ${WH.cardBorder}`,
                borderRadius: 6, padding: '3px 8px', pointerEvents: 'none',
              }}>
                <div>{t('ipranTopo.wm.sampled', {
                  at: tm?.sampledAt ? formatDateTime(tm.sampledAt) : t('ipranTopo.detail.na'),
                })}</div>
                <div>{t('ipranTopo.wm.coverage', {
                  m: tm?.matchedEdges ?? 0, n: tm?.totalEdges ?? 0,
                })}</div>
              </div>
            </>
          )}
        </div>

        {/* 覆盖不足是异常不是解释：常驻；口径走可展开说明 */}
        {footWarns.length > 0 && (
          <Alert type="warning" showIcon style={{ padding: '4px 12px' }} message={
            <div style={{ fontSize: 12, lineHeight: 1.45 }}>
              {footWarns.map((w, i) => <div key={i}>{w}</div>)}
            </div>
          } />
        )}
        <div style={{ flexShrink: 0 }}>
          <CalcChainNote title={t('ipranTopo.calc.title')} sections={calcSections} />
        </div>

        <Drawer
          placement="right"
          width="min(95vw, 45rem)"
          open={focusSafe >= 0 || selectedEdge >= 0}
          onClose={() => { setFocus(-1); setSelectedEdge(-1) }}
          title={focusSafe >= 0
            ? t('ipranTopo.focus.title', { name: nodes[focusSafe]?.name ?? '' })
            : t('ipranTopo.detail.linkTitle')}
          destroyOnClose
        >
          {(focusSafe >= 0 || selectedEdge >= 0) && (
            <TopologyDetails
              nodes={nodes} edges={edges} idx={idx} fault={fault}
              focus={focusSafe} selectedEdge={selectedEdge}
              onFocus={(i) => { setFocus(i); setSelectedEdge(-1) }}
            />
          )}
        </Drawer>
      </div>
    )
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)',
      /* flex:1 认领内容区剩余高度，下面的画布行才能把「工具条之下」吃满。
         父级 #main-content 是 flex 列，不写这个只按内容高度排，地图底下会空一截。 */
      flex: 1, minHeight: 0,
    }}>

      <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>
      <TopologyControls
        layer={layer} onLayer={changeLayer}
        layoutKey={layoutKey} onLayout={setLayoutKey}
        colorMode={colorMode} onColor={setColorMode}
        showAllLabels={showAllLabels} onAllLabels={setShowAllLabels}
        foldParallel={foldParallel} onFold={setFoldParallel}
        loading={loading} onRefresh={() => void load({ force: true })}
        onExport={doExport} canExport={!!resp?.nodes.length}
        metaLine={meta ? t('ipranTopo.meta.line', {
          sec: (meta.elapsedSec ?? 0).toFixed(2),
          at: formatDateTime(meta.fetchedAt),
          cached: meta.cached ? t('ipranTopo.meta.cached') : '',
        }) + (colorMode === 'traffic' && meta.traffic?.sampledAt
          ? t('ipranTopo.meta.traffic', { at: formatDateTime(meta.traffic.sampledAt) })
          : '') : null}
        query={query} onQuery={setQuery} onLocate={locate}
        tierFilter={tierFilter} onTier={setTierFilter}
        statusFilter={statusFilter} onStatus={setStatusFilter}
        onZoomIn={() => bumpView('in')} onZoomOut={() => bumpView('out')} onFit={() => bumpView('fit')}
        onWeathermap={() => setView('weathermap')}
      />

      {/* 取数失败 / 降级留在工具条下：用户正在看的数可能不是本次结果。 */}
      {error && <Alert type="error" showIcon message={error} />}
      {topWarns.length > 0 && (
        <Alert type="warning" showIcon message={
          <div>{topWarns.map((w, i) => <div key={i}>{w}</div>)}</div>
        } />
      )}
      </div>

      {/* ⑤ 画布左 75% + 右栏 25%。右栏先排统计卡，再排告警榜，两块同宽。
          画布行 flex:1 吃掉工具条之下、页底说明之上的高度。
          窄屏（两栏 basis 加起来放不下）自动换行，右栏落到画布下面。 */}
      <div style={{
        display: 'flex', gap: 12, alignItems: 'stretch', flexWrap: 'wrap',
        flex: 1, minHeight: 0,
      }}>
        <div style={{
          flex: '3 1 640px', minWidth: 0, minHeight: 0,
          display: 'flex', flexDirection: 'column',
        }}>
          <CardBase title={t('ipranTopo.canvas.title')}
            extra={<ExplainIcon text={t('ipranTopo.canvas.hint')} placement="bottomLeft" icon="question" />}
            style={{
              flex: 1, minHeight: 360, minWidth: 0,
              display: 'flex', flexDirection: 'column', overflow: 'hidden',
            }}
            bodyStyle={{
              flex: '1 1 0%', minHeight: 0, height: 'auto',
              display: 'flex', flexDirection: 'column', overflow: 'hidden',
            }}>
            {loading && !resp ? <Spin /> : nodes.length === 0 ? <Empty description={t('ipranTopo.empty')} /> : (
              <>
                {/* 绝对定位铺满卡片剩余高度。不能靠 svg 的 height:100%：
                    flex 分到的高度对百分比是不确定的，画布会塌成 0。 */}
                <div style={{ flex: '1 1 0%', minHeight: 0, position: 'relative' }}>
                  <div style={{ position: 'absolute', inset: 0 }}>
                    <TopoCanvas
                      fill
                      nodes={nodes} edges={edges} idx={idx} layout={layout}
                      colorMode={colorMode} focus={focusSafe}
                      showAllLabels={showAllLabels} foldParallel={foldParallel}
                      dimmed={dimmed} impacted={impacted} selectedEdge={selectedEdge}
                      markGeo={layoutKey === 'geo'}
                      trafficAt={meta?.traffic?.sampledAt ? formatDateTime(meta.traffic.sampledAt) : null}
                      viewCmd={viewCmd} resetKey={`${layer}:${layoutKey}`}
                      onToggleFocus={(i) => { setFocus((prev) => (prev === i ? -1 : i)); setSelectedEdge(-1) }}
                      onSelectEdge={(i) => { setSelectedEdge(i); setFocus(-1) }}
                      onBackgroundClick={() => { setFocus(-1); setSelectedEdge(-1) }}
                    />
                  </div>
                </div>
                <div style={{ flexShrink: 0 }}>
                  <TopologyLegend colorMode={colorMode} tierCounts={meta?.tierCounts} />
                </div>
              </>
            )}
          </CardBase>
          {layoutKey === 'geo' && nodes.length > 0 && (
            <div style={{ marginTop: 12, flexShrink: 0 }}>
              <TopoGeoMap nodes={nodes} impacted={impacted}
                onFocus={(i) => { setFocus(i); setSelectedEdge(-1) }} />
            </div>
          )}
        </div>
        <div style={{
          flex: '1 1 260px', minWidth: 260, maxWidth: '100%',
          minHeight: 0, alignSelf: 'stretch', overflow: 'hidden',
          display: 'flex', flexDirection: 'column', gap: 12,
        }}>
          <TopologyMetrics
            ne={ne} ext={ext} folded={folded} rawLinks={rawLinks}
            downNe={downNe} critNe={critNe} critPct={critPct}
            inLayer={inLayer} edges={edges} explain={explain} onDrill={openDrill}
          />
          {/* flex:1 把告警榜接到统计卡下面，两块加起来正好等于左侧地图的高度。
              名单超出时只在这张卡里滚动，不把地图顶高。 */}
          <CardBase
            style={{
              flex: '1 1 0%', minHeight: 0,
              display: 'flex', flexDirection: 'column', overflow: 'hidden',
            }}
            bodyStyle={{ flex: '1 1 0%', minHeight: 0, height: 'auto', overflow: 'auto' }}
            title={
            <span style={{ whiteSpace: 'normal', lineHeight: 1.35 }}>{t('ipranTopo.alarmTitle')}</span>
          }>
            {topRows.length
              ? (
                <>
                  <div style={{ fontSize: 12, color: WH.textDim, marginBottom: 6 }}>
                    {t('ipranTopo.alarmHint')}
                  </div>
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    fontSize: 12, color: WH.textDim, padding: '0 4px 4px',
                  }}>
                    <span style={{ flex: 1, minWidth: 0 }}>{t('ipranTopo.col.name')}</span>
                    <span>{t('ipranTopo.col.crit')} <ExplainIcon text={t('ipranTopo.critExplain')} /></span>
                  </div>
                  {topRows.map((r) => {
                    const sub = `${tierName(r.tier)} · ${r.site || '—'} · ${t('ipranTopo.col.major')} ${r.major} · ${t('ipranTopo.col.minor')} ${r.minor} · ${t('ipranTopo.col.warn')} ${r.warn}`
                    return (
                      <div key={r.id} style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        height: '3rem', padding: '0 4px', overflow: 'hidden',
                        borderTop: 'var(--wh-card-border)',
                      }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <a
                            title={r.name}
                            onClick={() => setFocus(idx.byId[r.id])}
                            style={{
                              display: 'block', fontWeight: 600, fontSize: 12, lineHeight: '1.1rem',
                              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                            }}
                          >{r.name}</a>
                          <div title={sub} style={{
                            color: WH.textDim, fontSize: 11, lineHeight: '1rem',
                            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                          }}>{sub}</div>
                        </div>
                        <ExplainPopover text={t('ipranTopo.critTip', {
                          name: r.name, crit: r.crit, major: r.major, minor: r.minor, warn: r.warn,
                        })}>
                          <b style={{
                            fontVariantNumeric: 'tabular-nums', fontSize: 16, lineHeight: 1,
                            color: r.crit > 0 ? WH.sev.Critical : WH.textMuted,
                          }}>{r.crit}</b>
                        </ExplainPopover>
                      </div>
                    )
                  })}
                </>
              )
              : <Empty description={t('ipranTopo.alarmEmpty')} />}
          </CardBase>
        </div>
      </div>

      {/* 缺坐标、近似匹配、告警饱和和口径说明都是解释，不是本次取数失败。
          放在画布下面，避免占掉工具条和地图之间的高度。内容仍常驻，不收进折叠区。 */}
      <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {footWarns.length > 0 && (
          <Alert type="warning" showIcon style={{ padding: '4px 12px' }} message={
            <div style={{ fontSize: 12, lineHeight: 1.45 }}>
              {footWarns.map((w, i) => <div key={i}>{w}</div>)}
            </div>
          } />
        )}
        <CalcChainNote title={t('ipranTopo.calc.title')} sections={calcSections} />
      </div>

      {/* 点节点 / 点链路后从右侧滑出，不占分栏。宽度与微波实时页、GIS 下钻抽屉一致。 */}
      <Drawer
        placement="right"
        width="min(95vw, 45rem)"
        open={focusSafe >= 0 || selectedEdge >= 0}
        onClose={() => { setFocus(-1); setSelectedEdge(-1) }}
        title={focusSafe >= 0
          ? t('ipranTopo.focus.title', { name: nodes[focusSafe]?.name ?? '' })
          : t('ipranTopo.detail.linkTitle')}
        destroyOnClose
      >
        {(focusSafe >= 0 || selectedEdge >= 0) && (
          <TopologyDetails
            nodes={nodes} edges={edges} idx={idx} fault={fault}
            focus={focusSafe} selectedEdge={selectedEdge}
            onFocus={(i) => { setFocus(i); setSelectedEdge(-1) }}
          />
        )}
      </Drawer>

      <TopoDrillModal
        kind={drill?.kind ?? null}
        anchor={drill?.anchor ?? null}
        nodes={nodes} edges={edges} idx={idx}
        onClose={() => setDrill(null)}
        onFocus={(i) => setFocus(i)}
      />
    </div>
  )
}






