/* ============================================================================
 * location/useCoverageGrid.ts —— 「覆盖 RSRP」图层的取数（只在打开开关后才拉）
 * ============================================================================
 *
 *   GET /api/ngi-coverage/meta              各 kpi 的格点数 / run_id / 范围 / 档位统计
 *   GET /api/ngi-coverage/grid?kpi=…        NGC1 二进制（≈1.6 MB，gzip 后约 0.4 MB），带 ETag
 *
 * 缓存分两层：
 *   · 模块级：同一次页面会话内，关掉再打开图层、切走再切回位置页都不重下、不重建索引；
 *     键里带 run_id，换了一轮同步（每周日）自然失效。
 *   · 浏览器：后端回 `Cache-Control: no-cache` + ETag，整页刷新后也只做一次 304 协商。
 * 失败的请求不进缓存（否则一次网络抖动会让图层在本次会话里永远打不开）。
 * ========================================================================== */
import { useEffect, useMemo, useState } from 'react'

import api from '../../api'
import { buildIndex, parseGridBlob, type BinStat, type GridIndex } from './coverageGrid'

export type CoverageKpi = {
  kpi: string
  rows: number
  run_id: number
  bbox: (number | null)[]
  bins: BinStat[]
  legend?: unknown
}

export type CoverageMeta = {
  available: boolean
  reason?: string
  grid_size_m?: number
  synced_at?: string | null
  task_log_name?: string | null
  kpis?: CoverageKpi[]
}

export type CoverageState = {
  meta: CoverageMeta | null
  kpi: CoverageKpi | null
  setKpi: (kpi: string) => void
  index: GridIndex | null
  cellM: number
  loading: boolean
  error: string
}

const META_TTL_MS = 10 * 60 * 1000
let metaCache: { at: number; p: Promise<CoverageMeta> } | null = null
const gridCache = new Map<string, Promise<GridIndex>>()

function errText(err: unknown): string {
  const anyErr = err as { response?: { status?: number; data?: unknown }; message?: string }
  const status = anyErr?.response?.status
  if (status) return `HTTP ${status}`
  return err instanceof Error ? err.message : String(err)
}

function loadMeta(): Promise<CoverageMeta> {
  const now = Date.now()
  if (metaCache && now - metaCache.at < META_TTL_MS) return metaCache.p
  const p = api.get<CoverageMeta>('/ngi-coverage/meta').then(({ data }) => data)
  metaCache = { at: now, p }
  p.catch(() => { if (metaCache?.p === p) metaCache = null })
  return p
}

function loadGrid(kpi: CoverageKpi): Promise<GridIndex> {
  const key = `${kpi.kpi}@${kpi.run_id}:${kpi.rows}`
  const hit = gridCache.get(key)
  if (hit) return hit
  const p = api
    .get<ArrayBuffer>('/ngi-coverage/grid', {
      params: { kpi: kpi.kpi },
      responseType: 'arraybuffer',
      timeout: 120000,
    })
    .then(({ data }) => new Promise<GridIndex>((resolve, reject) => {
      // 让出一帧再建索引（17.8 万点约几十 ms），避免和开关的点击反馈挤在同一帧
      window.setTimeout(() => {
        try { resolve(buildIndex(parseGridBlob(data))) } catch (e) { reject(e) }
      }, 0)
    }))
  gridCache.set(key, p)
  p.catch(() => { if (gridCache.get(key) === p) gridCache.delete(key) })
  return p
}

export function useCoverageGrid(enabled: boolean): CoverageState {
  const [meta, setMeta] = useState<CoverageMeta | null>(null)
  const [kpiName, setKpiName] = useState<string>('')
  const [loaded, setLoaded] = useState<{ key: string; ix: GridIndex } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!enabled || meta) return
    let alive = true
    setLoading(true)
    setError('')
    loadMeta()
      .then((m) => { if (alive) setMeta(m) })
      .catch((e: unknown) => { if (alive) setError(errText(e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [enabled, meta])

  const kpi = useMemo<CoverageKpi | null>(() => {
    const list = meta?.available ? meta.kpis || [] : []
    return list.find((k) => k.kpi === kpiName) || list[0] || null
  }, [meta, kpiName])

  // 切换 kpi 时旧索引不能配新颜色继续显示：只认与当前 kpi 同键的那份
  const kpiKey = kpi ? `${kpi.kpi}@${kpi.run_id}` : ''

  useEffect(() => {
    if (!enabled || !kpi) return
    let alive = true
    setLoading(true)
    setError('')
    loadGrid(kpi)
      .then((ix) => { if (alive) setLoaded({ key: `${kpi.kpi}@${kpi.run_id}`, ix }) })
      .catch((e: unknown) => { if (alive) { setError(errText(e)); setLoaded(null) } })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [enabled, kpi])

  return {
    meta,
    kpi,
    setKpi: setKpiName,
    index: loaded && loaded.key === kpiKey ? loaded.ix : null,
    cellM: meta?.grid_size_m || 50,
    loading,
    error,
  }
}
