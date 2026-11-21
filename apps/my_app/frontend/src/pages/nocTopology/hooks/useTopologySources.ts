/* ============================================================================
 * NOC 拓扑中心 —— 三来源取数与适配
 * ============================================================================
 * 职责：
 *   1. IPRAN / MW 各走一条独立的请求链（`Promise.allSettled` 语义：互不牵连）；
 *   2. OLIB 复用既有的 `useOlibSiteGeo` / `useOlibFiberLinks` 静态文件加载；
 *   3. 把三份原生数据交给 adapters/ 转成统一模型，**任何一份失败都不清空其他两份**。
 *
 * 刷新策略（与 docs/NOC_TOPOLOGY_DATA_CONTRACT.md 第六节一致）：
 *   · 首次加载：IPRAN 默认层 + MW 最新日 + OLIB 静态文件；
 *   · 切 IPRAN 层 / 切流量视图 / 切 MW 粒度 —— 只重取对应来源；
 *   · 定时刷新（顶栏事件）按来源最小间隔节流：IPRAN 300s、MW 900s、OLIB 1800s；
 *   · 手动刷新按钮 `force=true`，绕过节流与后端缓存。
 * ========================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { getIpranTopoGraph, getIpranTopoLayers, getMwUmeRealtime, getMwUmeTopology } from '../../../api'
import type { IpranLayerInfo, MwUmeTopologyResponse } from '../../../api'
import type { LayerKey, TopoResp } from '../../ipranTopo/types'
import { buildFiberLinks, buildGeoLinks, buildGeoSites } from '../../networkTopologyV2/geoData'
import { useOlibFiberLinks } from '../../networkTopologyV2/useFiberLinks'
import { useOlibSiteGeo } from '../../networkTopologyV2/useSiteGeo'
import { adaptIpran } from '../adapters/ipranAdapter'
import { adaptMw } from '../adapters/mwAdapter'
import { adaptOlib } from '../adapters/olibAdapter'
import type { NocModel, SourceKey, SourceSlice, SourceState } from '../types'

/** 来源级最小刷新间隔（毫秒）。低于这个间隔的自动刷新直接跳过。 */
export const MIN_REFRESH_MS: Record<SourceKey, number> = {
  ipran: 300_000,
  mw: 900_000,
  olib: 1_800_000,
}

export interface TopologySourcesOptions {
  layer: LayerKey
  /** 是否请求 `traffic=1`（只在流量视图为 true） */
  includeTraffic: boolean
  /** MW 走 15 分钟实时接口 */
  mwRealtime: boolean
}

export interface TopologySourcesResult {
  model: NocModel
  ipran: SourceSlice
  mw: SourceSlice
  olib: SourceSlice
  layers: IpranLayerInfo[]
  loading: boolean
  /** 最近一次成功取数时间（毫秒时间戳，供页面显示「刚刚更新」） */
  lastSuccessAt: number
  refresh: (opts?: { force?: boolean }) => Promise<void>
  reloadOlib: () => void
}

function errText(e: unknown): string {
  const anyE = e as { message?: string; response?: { data?: { detail?: string } } }
  return anyE?.response?.data?.detail || anyE?.message || String(e)
}

function ipranStateOf(resp: TopoResp, trafficRequested: boolean): SourceState {
  const meta = resp.meta
  if (meta.stale) return 'stale'
  const warnings = meta.warnings ?? []
  const cov = meta.traffic && meta.traffic.totalEdges > 0
    ? meta.traffic.matchedEdges / meta.traffic.totalEdges
    : 1
  if (warnings.length > 0) return 'partial'
  if (trafficRequested && cov < 1) return 'partial'
  return 'connected'
}

function mwStateOf(resp: MwUmeTopologyResponse): SourceState {
  return (resp.kpis?.unknown ?? 0) > 0 ? 'partial' : 'connected'
}

export function useTopologySources(opts: TopologySourcesOptions): TopologySourcesResult {
  const { layer, includeTraffic, mwRealtime } = opts

  /* ---------------- IPRAN ---------------- */
  const [ipranRaw, setIpranRaw] = useState<TopoResp | null>(null)
  const [ipranState, setIpranState] = useState<SourceState>('loading')
  const [ipranErr, setIpranErr] = useState<string | undefined>()
  const ipranSeq = useRef(0)
  const hasIpran = useRef(false)

  /* ---------------- MW ---------------- */
  const [mwRaw, setMwRaw] = useState<MwUmeTopologyResponse | null>(null)
  const [mwState, setMwState] = useState<SourceState>('loading')
  const [mwErr, setMwErr] = useState<string | undefined>()
  const mwSeq = useRef(0)
  const hasMw = useRef(false)

  /* ---------------- 层清单 ---------------- */
  const [layers, setLayers] = useState<IpranLayerInfo[]>([])
  useEffect(() => {
    let alive = true
    getIpranTopoLayers()
      .then((r) => { if (alive) setLayers((r.data?.layers ?? []) as IpranLayerInfo[]) })
      .catch(() => { if (alive) setLayers([]) })
    return () => { alive = false }
  }, [])

  /* ---------------- OLIB（静态资产，走既有 loader） ---------------- */
  const geo = useOlibSiteGeo()
  const fiber = useOlibFiberLinks()

  const lastAt = useRef<Record<SourceKey, number>>({ ipran: 0, mw: 0, olib: 0 })
  const [lastSuccessAt, setLastSuccessAt] = useState(0)

  const loadIpran = useCallback(async (mode: { force?: boolean; silent?: boolean } = {}) => {
    const seq = ++ipranSeq.current
    if (!mode.silent) { setIpranState('loading'); setIpranErr(undefined) }
    try {
      const r = await getIpranTopoGraph(layer, mode.force ? 1 : 0, includeTraffic ? 1 : 0)
      if (seq !== ipranSeq.current) return
      const resp = r.data as TopoResp
      setIpranRaw(resp)
      setIpranState(ipranStateOf(resp, includeTraffic))
      setIpranErr(undefined)
      hasIpran.current = true
      lastAt.current.ipran = Date.now()
      setLastSuccessAt(Date.now())
    } catch (e) {
      if (seq !== ipranSeq.current) return
      setIpranErr(errText(e))
      // 有旧数据就保留并标 stale（不能因为一次超时把整张图清空）
      setIpranState(hasIpran.current ? 'stale' : 'unavailable')
    }
  }, [layer, includeTraffic])

  const loadMw = useCallback(async (mode: { force?: boolean; silent?: boolean } = {}) => {
    const seq = ++mwSeq.current
    if (!mode.silent) { setMwState('loading'); setMwErr(undefined) }
    try {
      const call = mwRealtime ? getMwUmeRealtime : getMwUmeTopology
      const r = await call({ top_n: 20, refresh: mode.force ? 1 : 0 })
      if (seq !== mwSeq.current) return
      const resp = r.data as MwUmeTopologyResponse
      setMwRaw(resp)
      setMwState(mwStateOf(resp))
      setMwErr(undefined)
      hasMw.current = true
      lastAt.current.mw = Date.now()
      setLastSuccessAt(Date.now())
    } catch (e) {
      if (seq !== mwSeq.current) return
      setMwErr(errText(e))
      setMwState(hasMw.current ? 'stale' : 'unavailable')
    }
  }, [mwRealtime])

  /* 层 / 流量模式变化：立即重取（这是用户明确动作，不节流） */
  useEffect(() => { void loadIpran() }, [loadIpran])
  useEffect(() => { void loadMw() }, [loadMw])

  /* OLIB 首次读盘完成时记一次时间，避免第一次自动刷新又白读一遍静态文件 */
  useEffect(() => {
    if (!geo.loading && !fiber.loading) lastAt.current.olib = Date.now()
  }, [geo.loading, fiber.loading, geo.file, fiber.file])

  /* 来源级刷新：自动刷新走这里，按最小间隔跳过尚未到期的来源 */
  const refresh = useCallback(async (refreshOpts: { force?: boolean } = {}) => {
    const force = refreshOpts.force === true
    const now = Date.now()
    const due = (s: SourceKey) => force || now - lastAt.current[s] >= MIN_REFRESH_MS[s]

    const jobs: Promise<void>[] = []
    if (due('ipran')) jobs.push(loadIpran({ silent: true, force }))
    if (due('mw')) jobs.push(loadMw({ silent: true, force }))
    if (due('olib')) {
      lastAt.current.olib = now
      geo.reload()
      fiber.reload()
    }
    await Promise.allSettled(jobs)
  }, [loadIpran, loadMw, geo.reload, fiber.reload])

  /* ---------------- 适配 ---------------- */
  const ipran = useMemo(
    () => adaptIpran({ resp: ipranRaw, layer, state: ipranState, error: ipranErr, trafficRequested: includeTraffic }),
    [ipranRaw, layer, ipranState, ipranErr, includeTraffic],
  )

  const mw = useMemo(
    () => adaptMw({ resp: mwRaw, state: mwState, error: mwErr, realtime: mwRealtime }),
    [mwRaw, mwState, mwErr, mwRealtime],
  )

  const olibState: SourceState = geo.loading || fiber.loading
    ? (geo.file || fiber.file ? 'static' : 'loading')
    : (geo.error || fiber.error ? 'unavailable' : 'static')

  const olib = useMemo(() => {
    const sites = buildGeoSites(geo.file)
    const core = buildGeoLinks(sites)
    const fibers = buildFiberLinks(sites, fiber.file)
    return adaptOlib({
      sites,
      coreLinks: core.links,
      fiberLinks: fibers.links,
      skippedCore: core.skipped,
      skippedFiber: fibers.skipped,
      fiberStale: fibers.stale,
      geoGeneratedAt: geo.file?.generatedAt,
      state: olibState,
      error: geo.error || fiber.error || undefined,
    })
  }, [geo.file, geo.error, fiber.file, fiber.error, olibState])

  const model = useMemo<NocModel>(() => ({
    nodes: [...ipran.nodes, ...mw.nodes, ...olib.nodes],
    edges: [...ipran.edges, ...mw.edges, ...olib.edges],
    sources: [ipran.meta, mw.meta, olib.meta],
  }), [ipran, mw, olib])

  const loading = ipranState === 'loading' || mwState === 'loading'

  return {
    model,
    ipran,
    mw,
    olib,
    layers,
    loading,
    lastSuccessAt,
    refresh,
    reloadOlib: () => { geo.reload(); fiber.reload() },
  }
}
