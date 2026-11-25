/* ============================================================================
 * location/useLteCells.ts —— 「LTE 小区」/「5G NR 小区」图层取数（只在打开开关后才拉）
 * ============================================================================
 *   GET /api/ngi-coverage/lte-cells  —— app.ngi_lte_cell_param 全量，列式 JSON + 弱 ETag
 *   GET /api/ngi-coverage/nr-cells   —— app.ngi_nr_cell_param 全量，列名与 LTE 相同
 *
 * 模块级缓存（按制式）：同一次页面会话内关掉再开、切走再回来都不重下、不重建索引；
 * 失败的请求不进缓存（一次网络抖动不该让图层在本次会话里永远打不开）。
 *
 * NR 的扇面半径要按「NR + LTE 全部站点」的站间距算（见 lteCells.assignSiteRadii 的 refs）：
 * NR 站点稀疏，只看 NR 自己的话半径会顶到上限、盖住周围的 LTE 站。所以 NR 加载时会
 * 顺带取一次 LTE 工参（同一份缓存，LTE 图层已开过就不再下载）；LTE 取不到时退回只按 NR 算。
 * ========================================================================== */
import { useEffect, useState } from 'react'

import api from '../../api'
import {
  NR_FREQ_OVERRIDES, NR_PALETTE, NR_SCALE, assignSiteRadii, buildCellIndex, buildFreqStyles,
  parseCellPayload, sitePoints,
  type CellIndex, type CellPayload, type FreqStyle, type FreqStyleOptions, type LteCell,
} from './lteCells'

export type LteCellData = {
  index: CellIndex
  styles: FreqStyle[]
  styleMap: Map<string, FreqStyle>
  snapshotDate: string | null
  skipped: number
}

export type LteCellState = {
  data: LteCellData | null
  unavailable: string
  loading: boolean
  error: string
}

type Loaded = LteCellData | { unavailable: string }
type Fetched = { cells: LteCell[]; payload: CellPayload } | { unavailable: string }

const cache = new Map<string, Promise<Loaded>>()

function cached(key: string, run: () => Promise<Loaded>): Promise<Loaded> {
  const hit = cache.get(key)
  if (hit) return hit
  const p = run()
  cache.set(key, p)
  p.catch(() => { if (cache.get(key) === p) cache.delete(key) })
  return p
}

function fetchCells(url: string): Promise<Fetched> {
  return api.get<CellPayload>(url, { timeout: 120000 }).then(({ data }) => {
    if (!data?.available) return { unavailable: data?.reason || 'unavailable' }
    return { cells: parseCellPayload(data), payload: data }
  })
}

function finish(cells: LteCell[], payload: CellPayload, opts?: FreqStyleOptions): LteCellData {
  const styles = buildFreqStyles(cells, opts)
  return {
    index: buildCellIndex(cells),
    styles,
    styleMap: new Map(styles.map((s) => [s.key, s])),
    snapshotDate: payload.snapshot_date || null,
    skipped: Math.max(0, (payload.count || 0) - cells.length),
  }
}

function loadLte(): Promise<Loaded> {
  return cached('lte', () => fetchCells('/ngi-coverage/lte-cells')
    .then((r) => ('unavailable' in r ? r : finish(r.cells, r.payload))))
}

function loadNr(): Promise<Loaded> {
  return cached('nr', async () => {
    const r = await fetchCells('/ngi-coverage/nr-cells')
    if ('unavailable' in r) return r
    try {
      const lte = await loadLte()
      if (!('unavailable' in lte)) assignSiteRadii(r.cells, sitePoints(lte.index.cells))
    } catch {
      /* LTE 取不到：保留 parseCellPayload 里只按 NR 自身站间距算出的半径 */
    }
    return finish(r.cells, r.payload, { palette: NR_PALETTE, overrides: NR_FREQ_OVERRIDES, scale: NR_SCALE })
  })
}

function useCellData(load: () => Promise<Loaded>, enabled: boolean): LteCellState {
  const [data, setData] = useState<LteCellData | null>(null)
  const [unavailable, setUnavailable] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!enabled || data || unavailable) return
    let alive = true
    setLoading(true)
    setError('')
    load()
      .then((r) => {
        if (!alive) return
        if ('unavailable' in r) setUnavailable(r.unavailable)
        else setData(r)
      })
      .catch((e: unknown) => {
        if (!alive) return
        const status = (e as { response?: { status?: number } })?.response?.status
        setError(status ? `HTTP ${status}` : e instanceof Error ? e.message : String(e))
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [load, enabled, data, unavailable])

  return { data, unavailable, loading, error }
}

export function useLteCells(enabled: boolean): LteCellState {
  return useCellData(loadLte, enabled)
}

export function useNrCells(enabled: boolean): LteCellState {
  return useCellData(loadNr, enabled)
}
