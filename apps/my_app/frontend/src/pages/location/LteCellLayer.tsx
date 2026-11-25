/* ============================================================================
 * location/LteCellLayer —— 位置页「LTE 小区」/「5G NR 小区」扇区图层（NGI 工参）
 * ============================================================================
 *
 * 一个组件两层共用：`kind="lte"`（app.ngi_lte_cell_param）/ `kind="nr"`（app.ngi_nr_cell_param）。
 * 只在 LAYERS 浮层里打开对应开关后才挂载。四块：
 *   1. Canvas GridLayer：按瓦片画 65° 扇面（lteCells.drawCellsTile），频点决定颜色与半径，
 *      半径按站间距自适应。放在 tilePane：覆盖 RSRP 5 < LTE 6 < NR 7，都在站点 / 链路
 *      （overlayPane）之下。NR 用另一组颜色 + 虚线描边，两层同时打开也分得清。
 *   2. 点选：在 Leaflet 的 `preclick` 里做命中判断，把 `CellHit` 挂到原生事件上
 *      （见 mapDock.CELL_HIT_KEY）；两层都命中时 prio 高的（NR）留下，`click` 里只有它开弹窗。
 *      「覆盖 RSRP」看到这个标记就让路 —— 与监听器注册顺序无关。
 *   3. 悬浮提示：站点名 / 站点 ID / 扇区 / 本扇区全部载波；两层重叠时同样只出 prio 高的那个。
 *   4. 图例（右下停靠区）：每个频点一行（色块 / 频点 / Band / 小区数），可逐个勾选显隐。
 * ========================================================================== */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import L from 'leaflet'

import { isMapAlive, useMap } from '../../components/LeafletMap'
import { useI18n } from '../../i18n'
import { WH } from '../../theme'
import { CELL_HIT_KEY, CELL_HOVER_KEY, useRightDock, type CellMarkedEvent } from './mapDock'
import {
  SECTOR_DEG, bandLabel, drawCellsTile, hitCell, sameSectorCells, sectorOutline, sectorRadiusPx,
  type CellKind, type LteCell,
} from './lteCells'
import type { LteCellState } from './useLteCells'

const esc = (v: unknown) => String(v ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' } as Record<string, string>
)[c])
const dash = (v: unknown) => (v == null || v === '' ? '—' : esc(v))

/** 浮层 / 站点 / 链路上不做扇面命中（它们有自己的点击与提示） */
const BLOCK_SELECTOR = '.leaflet-interactive, .leaflet-popup, .loc-right-dock, #gis-layers-panel, .leaflet-control'

const LAYER = {
  lte: { prio: 1, zIndex: 6, dashed: false, arfcn: 'EARFCN', showTac: true },
  nr: { prio: 2, zIndex: 7, dashed: true, arfcn: 'SSB-ARFCN', showTac: false },
} as const

export function LteCellLayer({ state, kind = 'lte' }: { state: LteCellState; kind?: CellKind }) {
  const map = useMap()
  const { t } = useI18n()
  const cfg = LAYER[kind]
  const data = state.data
  const [hidden, setHidden] = useState<Set<string>>(() => new Set())
  const visible = useMemo<Set<string> | null>(() => {
    if (!data || hidden.size === 0) return null
    return new Set(data.styles.map((s) => s.key).filter((k) => !hidden.has(k)))
  }, [data, hidden])

  const visibleRef = useRef(visible)
  const layerRef = useRef<L.GridLayer | null>(null)

  /* ---------------- 1. Canvas 瓦片层（数据到手建一次；勾选频点时只 redraw） ---------------- */
  useEffect(() => {
    if (!isMapAlive(map) || !data) return
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
          drawCellsTile(ctx, data.index, data.styleMap, visibleRef.current,
            coords.z, coords.x, coords.y, size.x, 0.32, cfg.dashed)
        }
        return tile
      },
    })
    const layer = new (CanvasLayer as unknown as new (o: L.GridLayerOptions) => L.GridLayer)({
      zIndex: cfg.zIndex,
      updateWhenZooming: false,
      keepBuffer: 2,
      maxZoom: 22,
    })
    layer.addTo(map)
    layerRef.current = layer
    return () => { layer.remove(); layerRef.current = null }
  }, [map, data, cfg])

  useEffect(() => {
    visibleRef.current = visible
    layerRef.current?.redraw()
    // 勾掉频点后，压在被隐藏扇面上的悬浮提示要立刻消失（见 2b 的 onRedraw）
    if (isMapAlive(map)) map.fire('loc-cells-redraw')
  }, [visible, map])

  const tRef = useRef(t)
  useEffect(() => { tRef.current = t }, [t])

  /* ---------------- 2. 点选 ---------------- */
  useEffect(() => {
    if (!isMapAlive(map) || !data) return
    let highlight: L.Polygon | null = null
    let hlCell: LteCell | null = null
    let current: L.Popup | null = null
    const clearHighlight = () => { highlight?.remove(); highlight = null; hlCell = null }
    const drawHighlight = () => {
      if (!hlCell) return
      const s = data.styleMap.get(hlCell.freqKey)
      if (!s) return
      const z = map.getZoom()
      const pts = sectorOutline(hlCell, sectorRadiusPx(s, hlCell, z), z)
      if (highlight) highlight.setLatLngs(pts)
      else highlight = L.polygon(pts, { color: '#111827', weight: 2.2, fill: false, interactive: false }).addTo(map)
    }

    const onPreclick = (e: L.LeafletMouseEvent) => {
      const ev = e.originalEvent as CellMarkedEvent | undefined
      if (!ev) return
      const target = ev.target as Element | null
      if (target?.closest?.(BLOCK_SELECTOR)) return
      const prev = ev[CELL_HIT_KEY]
      if (prev && prev.prio >= cfg.prio) return
      const i = hitCell(data.index, data.styleMap, visibleRef.current, e.latlng.lat, e.latlng.lng, map.getZoom())
      if (i >= 0) ev[CELL_HIT_KEY] = { kind, prio: cfg.prio, i }
    }

    const onClick = (e: L.LeafletMouseEvent) => {
      const hit = (e.originalEvent as CellMarkedEvent | undefined)?.[CELL_HIT_KEY]
      if (!hit || hit.kind !== kind) return
      const c = data.index.cells[hit.i]
      const s = data.styleMap.get(c.freqKey)
      const tt = tRef.current
      clearHighlight()
      hlCell = c
      drawHighlight()
      const row = (k: string, v: string) =>
        `<tr><td style="color:#6b7280;padding:1px 8px 1px 0;white-space:nowrap;vertical-align:top">${esc(k)}</td><td>${v}</td></tr>`
      const tilt = c.mechTilt == null && c.elecTilt == null ? '—' : `${dash(c.mechTilt)} / ${dash(c.elecTilt)}`
      const band = bandLabel(c.band, kind)
      const cellRow = c.sector == null
        ? dash(c.cellId)
        : `${dash(c.cellId)} · ${esc(tt('loc.cell.popup.sector'))} ${dash(c.sector)}`
      const html = `<div style="font-size:12px;line-height:1.5">`
        + `<div style="font-weight:600;margin-bottom:4px;display:flex;align-items:center;gap:6px">`
        + `<span style="width:10px;height:10px;border-radius:2px;background:${s?.color || '#9ca3af'};display:inline-block"></span>`
        + `${esc(c.name || c.uid)}</div>`
        + `<table style="border-collapse:collapse">`
        + row(tt('loc.cell.popup.site'), `${dash(c.enbName)} (${dash(c.enbId)})`)
        + row(tt('loc.cell.popup.cell'), cellRow)
        + (cfg.showTac ? row('PCI / TAC', `${dash(c.pci)} / ${dash(c.tac)}`) : row('PCI', dash(c.pci)))
        + row(tt('loc.cell.popup.freq'), `${dash(c.freq)} MHz · ${cfg.arfcn} ${dash(c.earfcn)}${band ? ` · ${esc(band)}` : ''}${c.bw ? ` · ${esc(c.bw)}` : ''}`)
        + row(tt('loc.cell.popup.azimuth'), c.omni && c.az == null ? esc(tt('loc.cell.popup.omni')) : `${dash(c.az)}° (${SECTOR_DEG}°)`)
        + row(tt('loc.cell.popup.height'), c.height == null ? '—' : `${esc(c.height)} m`)
        + row(tt('loc.cell.popup.tilt'), tilt)
        + row(tt('loc.cell.popup.cover'), dash(c.coverType))
        + row(tt('loc.cell.popup.active'), `${dash(c.active)}${c.vendor ? ` · ${esc(c.vendor)}` : ''}`)
        + row(tt('loc.cell.popup.center'), `${esc(c.lat.toFixed(5))}, ${esc(c.lon.toFixed(5))}`)
        + `</table></div>`
      const popup = L.popup({ maxWidth: 340, autoPanPadding: [20, 20] })
        .setLatLng(e.latlng)
        .setContent(html)
      current = popup
      // 只有「当前这个」弹窗关闭才清高亮：openOn 会先关掉上一个弹窗，
      // 上一个的 remove 回调若不判断，会把刚画好的新高亮一起清掉
      popup.on('remove', () => { if (current === popup) { current = null; clearHighlight() } })
      popup.openOn(map)
    }

    map.on('preclick', onPreclick)
    map.on('click', onClick)
    map.on('zoomend', drawHighlight)
    return () => {
      map.off('preclick', onPreclick)
      map.off('click', onClick)
      map.off('zoomend', drawHighlight)
      clearHighlight()
    }
  }, [map, data, kind, cfg])

  /* ---------------- 2b. 悬浮提示（站点 ID、站名、扇区小区信息） ----------------
     mousemove 里**同步**做命中判断（桶索引，微秒级）并在原生事件上举手（CELL_HOVER_KEY = prio）；
     显示放到 requestAnimationFrame 里，那时所有图层的 mousemove 都已跑完，看到更高 prio
     就让路 —— LTE / NR 重叠时只出一个提示。拖动中、鼠标压在站点/链路/浮层上时不显示。 */
  useEffect(() => {
    if (!isMapAlive(map) || !data) return
    const container = map.getContainer()
    const tip = L.tooltip({ direction: 'top', offset: [0, -10], opacity: 0.96, className: 'loc-cell-tip' })
    let shown = -1
    let raf = 0
    let last: L.LeafletMouseEvent | null = null
    let lastHit = -1

    const hide = () => {
      if (shown >= 0) { map.closeTooltip(tip); shown = -1 }
      if (container.style.cursor) container.style.cursor = ''
    }

    const render = (i: number) => {
      const tt = tRef.current
      const c = data.index.cells[i]
      const ids = sameSectorCells(data.index, i).slice(0, 8)
      const carrier = (k: number) => {
        const o = data.index.cells[k]
        const color = data.styleMap.get(o.freqKey)?.color || '#9ca3af'
        const band = bandLabel(o.band, kind)
        return `<tr style="${k === i ? 'font-weight:600' : ''}">`
          + `<td style="padding:0 6px 0 0"><span style="width:8px;height:8px;border-radius:2px;background:${color};display:inline-block"></span></td>`
          + `<td style="padding:0 8px 0 0;white-space:nowrap">${dash(o.freq)} MHz${band ? ` · ${esc(band)}` : ''}</td>`
          + `<td style="padding:0 8px 0 0;white-space:nowrap">PCI ${dash(o.pci)}</td>`
          + `<td style="white-space:nowrap">${esc(o.name || o.cellId)} <span style="opacity:.6">(${dash(o.cellId)})</span></td>`
          + `</tr>`
      }
      let sectorTxt: string
      if (c.omni && c.az == null) sectorTxt = esc(tt('loc.cell.popup.omni'))
      else if (c.sector == null) sectorTxt = `${esc(tt('loc.cell.popup.azimuth'))} ${dash(c.az)}°`
      else sectorTxt = `${esc(tt('loc.cell.popup.sector'))} ${dash(c.sector)} · ${dash(c.az)}°`
      return `<div style="font-size:12px;line-height:1.5">`
        + `<div style="font-weight:600">${kind === 'nr' ? '5G · ' : ''}${dash(c.enbName)} <span style="opacity:.7">(${esc(tt('loc.cell.tip.siteId'))} ${dash(c.enbId)})</span></div>`
        + `<div style="opacity:.8;margin-bottom:2px">${sectorTxt} · ${esc(tt('loc.cell.tip.carriers', { n: ids.length }))}</div>`
        + `<table style="border-collapse:collapse">${ids.map(carrier).join('')}</table>`
        + `<div style="opacity:.55;margin-top:2px">${esc(tt('loc.cell.tip.click'))}</div>`
        + `</div>`
    }

    const tick = () => {
      raf = 0
      const e = last
      if (!e) return
      const ev = e.originalEvent as CellMarkedEvent | undefined
      if (lastHit < 0 || (ev?.[CELL_HOVER_KEY] ?? 0) > cfg.prio) { hide(); return }
      if (lastHit !== shown) {
        tip.setContent(render(lastHit))
        shown = lastHit
      }
      tip.setLatLng(e.latlng)
      if (!map.hasLayer(tip)) map.openTooltip(tip)
      container.style.cursor = 'pointer'
    }

    const onMove = (e: L.LeafletMouseEvent) => {
      last = e
      lastHit = -1
      const ev = e.originalEvent as CellMarkedEvent | undefined
      const target = ev?.target as Element | null
      if (!(ev && ev.buttons !== 0) && !target?.closest?.(BLOCK_SELECTOR)) {
        lastHit = hitCell(data.index, data.styleMap, visibleRef.current, e.latlng.lat, e.latlng.lng, map.getZoom())
        if (lastHit >= 0 && ev && (ev[CELL_HOVER_KEY] ?? 0) < cfg.prio) ev[CELL_HOVER_KEY] = cfg.prio
      }
      if (!raf) raf = window.requestAnimationFrame(tick)
    }
    const onLeave = () => { last = null; lastHit = -1; hide() }
    const onRedraw = () => { if (last) onMove(last) }

    map.on('mousemove', onMove)
    map.on('mouseout', onLeave)
    map.on('movestart zoomstart', onLeave)
    map.on('loc-cells-redraw', onRedraw)
    return () => {
      if (raf) window.cancelAnimationFrame(raf)
      map.off('mousemove', onMove)
      map.off('mouseout', onLeave)
      map.off('movestart zoomstart', onLeave)
      map.off('loc-cells-redraw', onRedraw)
      hide()
    }
  }, [map, data, kind, cfg])

  /* ---------------- 3. 图例 ---------------- */
  const host = useRightDock(map)
  const boxRef = useRef<HTMLDivElement | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    L.DomEvent.disableClickPropagation(el)
    L.DomEvent.disableScrollPropagation(el)
  }, [host])

  if (!host) return null

  const box: CSSProperties = {
    pointerEvents: 'auto', minWidth: 200, maxWidth: 260, padding: '7px 9px', borderRadius: 8,
    background: WH.cardBg, border: WH.cardBorder, boxShadow: '0 2px 8px rgba(0,0,0,0.16)',
    color: WH.text, fontSize: 11, lineHeight: 1.6, overflowY: 'auto', maxHeight: kind === 'nr' ? 220 : 320,
  }
  const dim: CSSProperties = { color: WH.textDim }
  const linkBtn: CSSProperties = {
    border: 'none', background: 'transparent', color: WH.textDim, cursor: 'pointer',
    padding: 0, fontSize: 11, textDecoration: 'underline',
  }

  // 文案键写成字面量（不拼接），i18n 漏键守卫才能看见
  const title = kind === 'nr' ? t('loc.nr.title') : t('loc.cell.title')
  let body: ReactNode
  if (state.error) {
    body = <div style={{ color: '#d7191c' }}>{kind === 'nr' ? t('loc.nr.error', { msg: state.error }) : t('loc.cell.error', { msg: state.error })}</div>
  } else if (state.unavailable) {
    body = <div style={dim}>{kind === 'nr' ? t('loc.nr.unavailable', { msg: state.unavailable }) : t('loc.cell.unavailable', { msg: state.unavailable })}</div>
  } else if (!data) {
    body = <div style={dim}>{kind === 'nr' ? t('loc.nr.loading') : t('loc.cell.loading')}</div>
  } else {
    const total = data.index.cells.length
    body = (
      <>
        {data.styles.map((s) => {
          const band = bandLabel(s.band, kind)
          return (
            <label key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={!hidden.has(s.key)}
                onChange={(e) => setHidden((prev) => {
                  const next = new Set(prev)
                  if (e.target.checked) next.delete(s.key)
                  else next.add(s.key)
                  return next
                })}
                style={{ margin: 0, cursor: 'pointer' }}
              />
              <svg width="14" height="12" viewBox="0 0 14 12" style={{ flex: '0 0 auto' }}>
                <path
                  d="M7 11 L1.6 2.2 A7.5 7.5 0 0 1 12.4 2.2 Z"
                  fill={s.color} fillOpacity={0.45} stroke={s.color}
                  strokeDasharray={cfg.dashed ? '2 1.5' : undefined}
                />
              </svg>
              <span style={{ flex: 1 }}>
                {s.freq == null ? t('loc.cell.unknownFreq') : s.label}
                {band ? <span style={dim}> · {band}</span> : null}
              </span>
              <span style={dim}>{s.count}</span>
            </label>
          )
        })}
        <div style={{ display: 'flex', gap: 10, marginTop: 2 }}>
          <button type="button" style={linkBtn} onClick={() => setHidden(new Set())}>{t('loc.cell.all')}</button>
          <button type="button" style={linkBtn} onClick={() => setHidden(new Set(data.styles.map((s) => s.key)))}>
            {t('loc.cell.none')}
          </button>
        </div>
        <div style={{ ...dim, marginTop: 3 }}>
          {t('loc.cell.summary', { n: total.toLocaleString(), at: data.snapshotDate ? data.snapshotDate.slice(0, 10) : '—' })}
        </div>
        <div style={dim}>{t('loc.cell.hint', { deg: SECTOR_DEG })}</div>
        {data.skipped > 0 && <div style={dim}>{t('loc.cell.skipped', { n: data.skipped })}</div>}
      </>
    )
  }

  return createPortal(
    <div ref={boxRef} className="loc-cell-legend" style={box}>
      <div
        style={{ fontWeight: 600, marginBottom: collapsed ? 0 : 2, cursor: 'pointer', display: 'flex', justifyContent: 'space-between', gap: 8 }}
        onClick={() => setCollapsed((v) => !v)}
        title={t('loc.cell.toggleLegend')}
      >
        <span>{title}</span>
        <span style={dim}>{collapsed ? '▸' : '▾'}</span>
      </div>
      {!collapsed && body}
    </div>,
    host,
  )
}
