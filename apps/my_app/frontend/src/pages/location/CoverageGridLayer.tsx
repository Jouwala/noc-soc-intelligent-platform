/* ============================================================================
 * location/CoverageGridLayer —— 位置页「覆盖 RSRP」图层（NGI 50 m 网格）
 * ============================================================================
 *
 * 三块，全部只在 LAYERS 浮层里打开「覆盖 RSRP」后才挂载：
 *   1. Canvas GridLayer：按 256 px 瓦片只画落在瓦片里的格点（coverageGrid.drawTile），
 *      17.8 万点不建任何 Leaflet 对象 / React 组件。放在 tilePane（zIndex 5），
 *      即压在 PMTiles 底图之上、站点 / 链路等矢量层（overlayPane）之下，不挡点击。
 *   2. 点击查详情：本地索引先判「点到的是哪一格」，再调 /ngi-coverage/point 取
 *      小区 / PCI / 采样点 / 好点率（这些列不进大包）。点到站点、链路、轨迹点时
 *      （.leaflet-interactive）不弹，避免和它们自己的抽屉打架。
 *   3. 图例浮层（右下）：档位配色、格点数、同步时间、「定位到覆盖区」、多 kpi 切换。
 *
 * 为什么不是 <Polygon> 逐个画：见 coverageGrid.ts 文件头。
 * ========================================================================== */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import L from 'leaflet'

import api from '../../api'
import { isMapAlive, useMap } from '../../components/LeafletMap'
import { useI18n } from '../../i18n'
import { WH } from '../../theme'
import { bboxToBounds, binColors, drawTile, nearestCell, COVERAGE_NA_COLOR } from './coverageGrid'
import { CELL_HIT_KEY, useRightDock } from './mapDock'
import type { CoverageState } from './useCoverageGrid'

type PointResp = {
  found: boolean
  error?: string
  cell?: {
    earthid?: string
    offsetx?: number
    offsety?: number
    bin?: number
    rsrp?: number | null
    good_ratio?: number | null
    top_sample_points?: number | null
    total_sample_points?: number | null
    lon?: number | null
    lat?: number | null
    cells?: { key: string; name?: string | null }[]
    pcis?: string[]
  }
}

const esc = (v: unknown) => String(v ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' } as Record<string, string>
)[c])

const fmt1 = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? '—' : v.toFixed(1))

export function CoverageGridLayer({ state, opacity = 0.62 }: { state: CoverageState; opacity?: number }) {
  const map = useMap()
  const { t } = useI18n()
  const { index, kpi, cellM } = state
  const colors = useMemo(() => binColors(kpi?.bins || []), [kpi])

  /* ---------------- 1. Canvas 瓦片层 ---------------- */
  useEffect(() => {
    if (!isMapAlive(map) || !index) return
    const CanvasLayer = L.GridLayer.extend({
      createTile(this: L.GridLayer, coords: L.Coords) {
        const tile = document.createElement('canvas')
        const size = this.getTileSize()
        const dpr = Math.min(2, window.devicePixelRatio || 1)
        tile.width = size.x * dpr
        tile.height = size.y * dpr
        tile.style.width = `${size.x}px`
        tile.style.height = `${size.y}px`
        const ctx = tile.getContext('2d')
        if (ctx) {
          ctx.scale(dpr, dpr)
          drawTile(ctx, index, colors, coords.z, coords.x, coords.y, size.x, cellM)
        }
        return tile
      },
    })
    const layer = new (CanvasLayer as unknown as new (o: L.GridLayerOptions) => L.GridLayer)({
      opacity,
      zIndex: 5,
      // 缩放动画中不重画（避免 17.8 万点在每一帧重算），落定后再画
      updateWhenZooming: false,
      keepBuffer: 2,
      maxZoom: 22,
    })
    layer.addTo(map)
    return () => { layer.remove() }
  }, [map, index, colors, cellM, opacity])

  /* ---------------- 2. 点击查详情 ---------------- */
  const tRef = useRef(t)
  useEffect(() => { tRef.current = t }, [t])
  useEffect(() => {
    if (!isMapAlive(map) || !index || !kpi) return
    let seq = 0
    let highlight: L.Rectangle | null = null
    let current: L.Popup | null = null
    const clearHighlight = () => { if (highlight) { highlight.remove(); highlight = null } }

    const onClick = (e: L.LeafletMouseEvent) => {
      // 「LTE 小区」图层在 preclick 里已命中扇面时让路（见 LteCellLayer 文件头第 2 点）
      if ((e.originalEvent as (MouseEvent & Record<string, unknown>) | undefined)?.[CELL_HIT_KEY] != null) return
      const target = e.originalEvent?.target as Element | null
      if (target?.closest?.('.leaflet-interactive, .loc-cov-legend, .leaflet-popup, .loc-right-dock, #gis-layers-panel')) return
      const i = nearestCell(index, e.latlng.lat, e.latlng.lng, cellM)
      if (i < 0) return
      const lat = index.grid.lat[i]
      const lon = index.grid.lon[i]
      const tt = tRef.current

      clearHighlight()
      const dLat = cellM / 2 / 110540
      const dLon = cellM / 2 / (111320 * Math.cos((lat * Math.PI) / 180))
      highlight = L.rectangle([[lat - dLat, lon - dLon], [lat + dLat, lon + dLon]], {
        color: '#111827', weight: 2, fill: false, interactive: false,
      }).addTo(map)

      const popup = L.popup({ maxWidth: 320, autoPanPadding: [20, 20] })
        .setLatLng([lat, lon])
        .setContent(`<div style="font-size:12px">${esc(tt('loc.cov.popup.loading'))}</div>`)
      current = popup
      // 只有当前弹窗关闭才清高亮：openOn 会先关掉上一个弹窗，否则会把新高亮一起清掉
      popup.on('remove', () => { if (current === popup) { current = null; clearHighlight() } })
      popup.openOn(map)

      const my = ++seq
      api.get<PointResp>('/ngi-coverage/point', {
        params: { kpi: kpi.kpi, lat, lon, grid_size_m: cellM },
      }).then(({ data }) => {
        if (my !== seq || !map.hasLayer(popup)) return
        if (!data?.found || !data.cell) {
          popup.setContent(`<div style="font-size:12px">${esc(data?.error
            ? tt('loc.cov.popup.error', { msg: data.error })
            : tt('loc.cov.popup.notFound'))}</div>`)
          return
        }
        const c = data.cell
        const cells = (c.cells || []).map((x) => esc(x.name ? `${x.name} (${x.key})` : x.key)).join('<br/>') || '—'
        const color = colors[c.bin ?? 0] || COVERAGE_NA_COLOR
        const row = (k: string, v: string) =>
          `<tr><td style="color:#6b7280;padding:1px 8px 1px 0;white-space:nowrap;vertical-align:top">${esc(k)}</td><td>${v}</td></tr>`
        popup.setContent(
          `<div style="font-size:12px;line-height:1.5">`
          + `<div style="font-weight:600;margin-bottom:4px;display:flex;align-items:center;gap:6px">`
          + `<span style="width:10px;height:10px;border-radius:2px;background:${color};display:inline-block"></span>`
          + `${esc(kpi.kpi)} · ${esc(fmt1(c.rsrp))} dBm</div>`
          + `<table style="border-collapse:collapse">`
          + row(tt('loc.cov.popup.good'), c.good_ratio == null ? '—' : `${esc(c.good_ratio.toFixed(2))}%`)
          + row(tt('loc.cov.popup.samples'), `${esc(c.top_sample_points ?? '—')} / ${esc(c.total_sample_points ?? '—')}`)
          + row(tt('loc.cov.popup.cells'), cells)
          + row(tt('loc.cov.popup.pci'), esc((c.pcis || []).join(', ') || '—'))
          + row(tt('loc.cov.popup.grid'), `${esc(c.earthid)} / ${esc(c.offsetx)}, ${esc(c.offsety)}`)
          + row(tt('loc.cov.popup.center'), `${esc(lat.toFixed(5))}, ${esc(lon.toFixed(5))}`)
          + `</table></div>`,
        )
      }).catch((err: unknown) => {
        if (my !== seq || !map.hasLayer(popup)) return
        popup.setContent(`<div style="font-size:12px">${esc(tt('loc.cov.popup.error', {
          msg: err instanceof Error ? err.message : String(err),
        }))}</div>`)
      })
    }

    map.on('click', onClick)
    return () => {
      map.off('click', onClick)
      clearHighlight()
      map.closePopup()
    }
  }, [map, index, kpi, cellM, colors])

  /* ---------------- 3. 图例浮层 ---------------- */
  return <CoverageLegend state={state} colors={colors} />
}

function CoverageLegend({ state, colors }: { state: CoverageState; colors: Record<number, string> }) {
  const map = useMap()
  const { t } = useI18n()
  const host = useRightDock(map)
  const [zoom, setZoom] = useState(() => (isMapAlive(map) ? map.getZoom() : 11))
  const boxRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!isMapAlive(map)) return
    const onZoom = () => setZoom(map.getZoom())
    map.on('zoomend', onZoom)
    return () => { map.off('zoomend', onZoom) }
  }, [map])

  // 原生阻断：React 合成事件的 stopPropagation 拦不住 Leaflet 挂在容器上的原生监听
  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    L.DomEvent.disableClickPropagation(el)
    L.DomEvent.disableScrollPropagation(el)
  }, [host])

  if (!host) return null
  const { meta, kpi, loading, error } = state
  const kpis = meta?.kpis || []
  const total = kpi?.rows || 0
  const bins = [...(kpi?.bins || [])].sort((a, b) => (b.rsrp_avg ?? -999) - (a.rsrp_avg ?? -999))
  const bounds = bboxToBounds(kpi?.bbox)

  const box: CSSProperties = {
    pointerEvents: 'auto',
    minWidth: 190, maxWidth: 240, padding: '7px 9px', borderRadius: 8,
    background: WH.cardBg, border: WH.cardBorder, boxShadow: '0 2px 8px rgba(0,0,0,0.16)',
    color: WH.text, fontSize: 11, lineHeight: 1.6,
  }
  const dim: CSSProperties = { color: WH.textDim }
  const btn: CSSProperties = {
    marginTop: 4, padding: '1px 8px', fontSize: 11, borderRadius: 4, cursor: 'pointer',
    border: WH.cardBorder, background: 'transparent', color: WH.text,
  }

  let body: ReactNode
  if (error) {
    body = <div style={{ color: '#d7191c' }}>{t('loc.cov.error', { msg: error })}</div>
  } else if (meta && !meta.available) {
    body = <div style={dim}>{t('loc.cov.unavailable', { msg: meta.reason || '' })}</div>
  } else if (!kpi || (loading && !state.index)) {
    body = <div style={dim}>{t('loc.cov.loading')}</div>
  } else {
    body = (
      <>
        {kpis.length > 1 && (
          <select
            value={kpi.kpi}
            onChange={(e) => state.setKpi(e.target.value)}
            style={{ width: '100%', fontSize: 11, marginBottom: 4 }}
          >
            {kpis.map((k) => <option key={k.kpi} value={k.kpi}>{k.kpi}</option>)}
          </select>
        )}
        {bins.map((b) => (
          <div key={b.bin} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{
              width: 11, height: 11, borderRadius: 2, flex: '0 0 auto',
              background: colors[b.bin] || COVERAGE_NA_COLOR, display: 'inline-block',
            }} />
            <span style={{ flex: 1 }}>
              {b.rsrp_min == null ? t('loc.cov.noValue') : `${fmt1(b.rsrp_min)} ~ ${fmt1(b.rsrp_max)} dBm`}
            </span>
            <span style={dim}>{total ? `${((b.rows / total) * 100).toFixed(1)}%` : ''}</span>
          </div>
        ))}
        <div style={{ ...dim, marginTop: 3 }}>
          {t('loc.cov.summary', {
            n: total.toLocaleString(),
            at: meta?.synced_at ? String(meta.synced_at).slice(0, 10) : '—',
          })}
        </div>
        <div style={dim}>{zoom < 13 ? t('loc.cov.zoomHint') : t('loc.cov.clickHint')}</div>
        {bounds && (
          <button
            type="button"
            style={btn}
            onClick={() => map.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 })}
          >
            {t('loc.cov.fit')}
          </button>
        )}
      </>
    )
  }

  return createPortal(
    <div ref={boxRef} className="loc-cov-legend" style={box}>
      <div style={{ fontWeight: 600, marginBottom: 2 }}>{t('loc.cov.title')}</div>
      {body}
    </div>,
    host,
  )
}
