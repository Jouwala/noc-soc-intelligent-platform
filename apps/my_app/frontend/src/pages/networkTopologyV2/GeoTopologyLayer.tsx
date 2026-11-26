/* ============================================================================
 * OLIB GIS 拓扑 —— Leaflet 站点/链路图层
 * ============================================================================
 * 只负责「把已定位的站点与骨干链路画到地图上」，不持有页面状态。
 *
 * 关键行为
 *   · 站点按**屏幕像素网格**聚合（缩放/平移结束后重算），避免蒙罗维亚 110 个点
 *     叠成一团；点聚合圈 → fitBounds 展开。
 *   · 站点图标按类型区分：核心=紫色圆环、ISP=蓝色菱形、5G=红色圆点、普通=灰点。
 *     只有选中站点有脉冲光环（不给 667 个点同时跑动画）。
 *   · 站名分级显示：核心站名始终显示；zoom≥8 再显 ISP/5G；zoom≥10 显示全部；
 *     选中站点始终显示。更低倍率用悬浮 tooltip 看名字。
 *   · 链路是**连接关系示意直线**（见 geoData.ts 顶部说明），同端点多条链路
 *     做垂直偏移；点线可看两端站点与链路类型。
 *   · 所有 addTo 之前过 isMapAlive()：地图销毁后 appendChild(undefined) 会打崩整页。
 * ========================================================================== */
import { useEffect } from 'react'
import L from 'leaflet'
import { isMapAlive, useMap } from '../../components/LeafletMap'
import type { CoreLinkType } from '../networkTopology/topologyData'
import { siteKind } from './geoData'
import { FIBER_STYLES, type FiberStyle } from './fiberMeta'
import type { FiberLink, GeoLink, GeoSite } from './types'

const esc = (s: string) => s
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;')

const DASH: Record<CoreLinkType, string | undefined> = {
  exist: undefined,
  dwdm: '7 5',
  rail: '3 5',
  p25a: '10 4 3 4',
}

/** 像素聚合网格：zoom 越低格子越大；zoom≥11.5 不再聚合。 */
function bucketSizeOf(zoom: number): number {
  if (zoom < 8.5) return 56
  if (zoom < 9.5) return 46
  if (zoom < 10.5) return 38
  if (zoom < 11) return 30
  return 0
}

/** 线宽随地图缩放**连续**自适应（不是分档跳变）。
 *  Leaflet 的 weight 是屏幕像素、本身不随缩放变化，所以这里按 zoom 换算一个系数：
 *  以全国视图 LB_ZOOM=8 为基准 1.0（此时的线宽已按「全国视图看得清」定好），
 *  每放大一级 ×1.15、每缩小一级 ÷1.15，夹在 [0.7, 2.0]：
 *    z6 ≈ 0.76 · z7 ≈ 0.87 · z8 = 1 · z9 ≈ 1.15 · z10 ≈ 1.32 · z11 ≈ 1.52 · z12 ≈ 1.75 · z13+ = 2
 *  放大后线跟着变粗、缩小后变细，但不会细到看不见，也不会粗到糊成一片。 */
const LINE_BASE_ZOOM = 8
function zoomScale(zoom: number): number {
  return Math.min(2, Math.max(0.7, Math.pow(1.15, zoom - LINE_BASE_ZOOM)))
}

/** 全国视图（系数 1.0）下各图层的基准线宽倍数：fiberMeta 里的 weight 是按「放大后」
 *  定的细线值（图例也用它），全国视图需要再放大才看得清。 */
const FIBER_NATIONAL = 1.5
const FIBER_BASE_NATIONAL = 1.3
const BACKBONE_NATIONAL_WEIGHT = 3.2

/** 缩放过程中每帧最多重算一次（滚轮/触控板/捏合会连续触发 zoom 事件），
 *  zoomend 再兜底算一次，保证停下来时一定是最终线宽。 */
function onZoomChange(map: L.Map, fn: () => void): () => void {
  let raf = 0
  const tick = () => {
    if (raf) return
    raf = requestAnimationFrame(() => { raf = 0; fn() })
  }
  map.on('zoom', tick)
  map.on('zoomend', fn)
  return () => {
    if (raf) cancelAnimationFrame(raf)
    map.off('zoom', tick)
    map.off('zoomend', fn)
  }
}

/** 同端点多条骨干的平行偏移按**屏幕像素**算（原来按 0.004° 算，全国视图下不到 1px，几条线完全叠在一起）。 */
function offsetEnds(map: L.Map, link: GeoLink, zoom: number, weight: number): L.LatLng[] {
  const a = L.latLng(link.a.lat as number, link.a.lng as number)
  const b = L.latLng(link.b.lat as number, link.b.lng as number)
  if (!link.offset) return [a, b]
  const pa = map.project(a, zoom)
  const pb = map.project(b, zoom)
  const dx = pb.x - pa.x
  const dy = pb.y - pa.y
  const len = Math.hypot(dx, dy) || 1
  const step = (weight + 1.5) * link.offset
  const o = L.point((-dy / len) * step, (dx / len) * step)
  return [map.unproject(pa.add(o), zoom), map.unproject(pb.add(o), zoom)]
}

export interface GeoTopologyLayerProps {
  sites: GeoSite[]
  links: GeoLink[]
  /** PDF 第 1 页数字化出来的链路（背景网，细线） */
  fiberLinks: FiberLink[]
  selected: GeoSite | null
  focus: { index: number; token: number; zoom?: number } | null
  onSelect: (index: number) => void
  typeLabel: (site: GeoSite) => string
  clusterLabel: (count: number) => string
  linkTypeLabel: (type: CoreLinkType) => string
  /** 深色主题：决定线条描边（halo）颜色 */
  isDark?: boolean
}

export default function GeoTopologyLayer({
  sites, links, fiberLinks, selected, focus, onSelect, typeLabel, clusterLabel, linkTypeLabel,
  isDark = false,
}: GeoTopologyLayerProps) {
  const map = useMap()

  /* ---------- PDF 链路（第 1 页数字化，细线背景网） ----------
     单独一个 pane（zIndex 390）压在 overlayPane(400) 之下：这一层是底衬，
     既不能盖住站点圆点，也不能因为开关重建而跑到粗骨干线上面去。 */
  useEffect(() => {
    if (!isMapAlive(map)) return
    if (!map.getPane('olibFiberPane')) {
      const pane = map.createPane('olibFiberPane')
      pane.style.zIndex = '390'
    }
    /* 两个 group：所有描边先画、所有彩线后画，避免后画的描边在交叉处把先画的彩线"切断" */
    const casingGroup = L.layerGroup().addTo(map)
    const lineGroup = L.layerGroup().addTo(map)
    const halo = isDark ? '#0a1428' : '#ffffff'
    const items: { line: L.Polyline; casing: L.Polyline | null; style: FiberStyle; isBase: boolean }[] = []
    for (const link of fiberLinks) {
      const isBase = !FIBER_STYLES[link.group] || link.group === 'base'
      const style = FIBER_STYLES[link.group] ?? FIBER_STYLES.base
      const latlngs: L.LatLngExpression[] = [
        [link.a.lat as number, link.a.lng as number],
        [link.b.lat as number, link.b.lng as number],
      ]
      /* 基础链路数量多且密，不加描边，否则蒙罗维亚一带会糊成一片白 */
      const casing = isBase ? null : L.polyline(latlngs, {
        pane: 'olibFiberPane', color: halo, weight: style.weight + 2, opacity: 0,
        lineCap: 'round', interactive: false,
      })
      casing?.addTo(casingGroup)
      const line = L.polyline(latlngs, {
        pane: 'olibFiberPane',
        color: style.color,
        weight: style.weight,
        opacity: style.opacity,
        dashArray: link.dash ? (style.dash ?? '6 5') : undefined,
        lineCap: 'round',
      })
      line.bindTooltip(
        `<strong>${esc(link.a.name)}</strong> ↔ <strong>${esc(link.b.name)}</strong><br/>${esc(link.type)}`,
        { sticky: true, className: 'olibv2-tip', opacity: 0.96 },
      )
      line.addTo(lineGroup)
      items.push({ line, casing, style, isBase })
    }

    /* 线宽 = 全国基准 × zoomScale(zoom)，随缩放连续变化。
       基础链路颜色加深（#64748b → #475569），否则灰线压在灰色道路底图上分不出来。 */
    const restyle = () => {
      if (!isMapAlive(map)) return
      const k = zoomScale(map.getZoom())
      for (const it of items) {
        const { style } = it
        const weight = style.weight * (it.isBase ? FIBER_BASE_NATIONAL : FIBER_NATIONAL) * k
        const opacity = it.isBase ? Math.max(style.opacity, 0.65) : Math.max(style.opacity, 0.92)
        it.line.setStyle(it.isBase ? { weight, opacity, color: '#475569' } : { weight, opacity })
        it.casing?.setStyle({ weight: weight + 2 * Math.max(1, k), opacity: 0.75 })
      }
    }
    restyle()
    const offZoom = onZoomChange(map, restyle)
    return () => {
      offZoom()
      lineGroup.remove()
      casingGroup.remove()
    }
  }, [map, fiberLinks, isDark])

  /* ---------- 站点 + 聚合 ---------- */
  useEffect(() => {
    if (!isMapAlive(map)) return
    const group = L.layerGroup().addTo(map)
    const container = map.getContainer()
    const selectedIndex = selected?.index ?? -1

    const drawSite = (site: GeoSite) => {
      const kind = siteKind(site)
      const isSel = site.index === selectedIndex
      const html = `<div class="olibv2-site olibv2-site--${kind}${isSel ? ' is-selected' : ''}">`
        + '<span class="olibv2-dot"></span>'
        + `<span class="olibv2-name">${esc(site.name)}</span></div>`
      const icon = L.divIcon({ className: 'olibv2-site-icon', html, iconSize: [0, 0], iconAnchor: [0, 0] })
      const marker = L.marker([site.lat as number, site.lng as number], {
        icon, keyboard: false, riseOnHover: true, zIndexOffset: isSel ? 500 : 0,
      })
      marker.bindTooltip(
        `<strong>${esc(site.name)}</strong><br/>${esc(typeLabel(site))}`
        + (site.county ? ` · ${esc(site.county)}` : ''),
        { direction: 'top', offset: [0, -8], opacity: 0.96, className: 'olibv2-tip' },
      )
      marker.on('click', (ev: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(ev.originalEvent)
        onSelect(site.index)
      })
      marker.addTo(group)
    }

    const drawCluster = (members: GeoSite[]) => {
      const count = members.length
      const lat = members.reduce((n, s) => n + (s.lat as number), 0) / count
      const lng = members.reduce((n, s) => n + (s.lng as number), 0) / count
      const px = Math.min(62, 26 + Math.log2(count) * 7)
      const html = `<div class="olibv2-cluster" style="width:${px.toFixed(0)}px;height:${px.toFixed(0)}px">`
        + `<span>${count}</span></div>`
      const icon = L.divIcon({
        className: 'olibv2-cluster-icon', html,
        iconSize: [px, px], iconAnchor: [px / 2, px / 2],
      })
      const marker = L.marker([lat, lng], { icon, keyboard: false })
      marker.bindTooltip(clusterLabel(count), { direction: 'top', offset: [0, -px / 2], className: 'olibv2-tip' })
      marker.on('click', (ev: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(ev.originalEvent)
        const bounds = L.latLngBounds(members.map((s) => [s.lat as number, s.lng as number] as [number, number]))
        map.fitBounds(bounds, { padding: [52, 52], maxZoom: 13 })
      })
      marker.addTo(group)
    }

    const rebuild = () => {
      if (!isMapAlive(map)) return
      group.clearLayers()
      const zoom = map.getZoom()
      /* 默认全国视图 LB_ZOOM=8 就要能看到 ISP/5G；再放大到 10 显示全部站名 */
      container.classList.toggle('olibv2-labels-major', zoom >= 8)
      container.classList.toggle('olibv2-labels-all', zoom >= 10)

      const size = bucketSizeOf(zoom)
      if (!size) {
        for (const site of sites) if (site.located) drawSite(site)
        return
      }
      const buckets = new Map<string, GeoSite[]>()
      for (const site of sites) {
        if (!site.located) continue
        /* 骨干核心永远单独画：国家尺度下它们是这张网的主干，不能被卷进聚合圈。
           其它站点（含 ISP/5G）参与网格聚合，由聚合圈展开。 */
        if (site.core) continue
        const p = map.latLngToContainerPoint([site.lat as number, site.lng as number])
        const key = `${Math.floor(p.x / size)}:${Math.floor(p.y / size)}`
        const list = buckets.get(key)
        if (list) list.push(site)
        else buckets.set(key, [site])
      }
      for (const members of buckets.values()) {
        if (members.length === 1) drawSite(members[0])
        else drawCluster(members)
      }
      /* 核心站点最后画，保证不被聚合圈盖住 */
      for (const site of sites) if (site.located && site.core) drawSite(site)
    }

    rebuild()
    map.on('moveend', rebuild)
    map.on('zoomend', rebuild)
    return () => {
      map.off('moveend', rebuild)
      map.off('zoomend', rebuild)
      container.classList.remove('olibv2-labels-major', 'olibv2-labels-all')
      group.remove()
    }
  }, [map, sites, selected?.index, onSelect, typeLabel, clusterLabel, selected])

  /* ---------- 骨干链路（示意直线） ---------- */
  useEffect(() => {
    if (!isMapAlive(map)) return
    const casingGroup = L.layerGroup().addTo(map)
    const lineGroup = L.layerGroup().addTo(map)
    const halo = isDark ? '#0a1428' : '#ffffff'
    const items: { link: GeoLink; line: L.Polyline; casing: L.Polyline }[] = []

    for (const link of links) {
      const ends = offsetEnds(map, link, map.getZoom(), BACKBONE_NATIONAL_WEIGHT)
      const label = linkTypeLabel(link.type)
      const casing = L.polyline(ends, {
        color: halo, weight: BACKBONE_NATIONAL_WEIGHT + 2.6, opacity: 0.8, lineCap: 'round', interactive: false,
      })
      const line = L.polyline(ends, {
        color: link.color,
        weight: BACKBONE_NATIONAL_WEIGHT,
        opacity: 0.95,
        dashArray: DASH[link.type],
        lineCap: 'round',
      })
      line.bindTooltip(
        `<strong>${esc(link.a.name)}</strong> ↔ <strong>${esc(link.b.name)}</strong><br/>${esc(label)}`,
        { sticky: true, className: 'olibv2-tip', opacity: 0.96 },
      )
      line.bindPopup(
        `<div class="olibv2-link-pop"><strong>${esc(link.a.name)}</strong> ↔ `
        + `<strong>${esc(link.b.name)}</strong><br/>${esc(label)}</div>`,
      )
      casing.addTo(casingGroup)
      line.addTo(lineGroup)
      items.push({ link, line, casing })
    }

    /* 线宽 = 3.2px × zoomScale(zoom)，随缩放连续变化（z6≈2.4px · z8=3.2px · z10≈4.2px · z12≈5.6px）；
       同端点多条骨干的像素偏移跟着线宽一起重算，任何缩放级别都不叠线。 */
    const restyle = () => {
      if (!isMapAlive(map)) return
      const zoom = map.getZoom()
      const weight = BACKBONE_NATIONAL_WEIGHT * zoomScale(zoom)
      for (const it of items) {
        const ends = offsetEnds(map, it.link, zoom, weight)
        it.casing.setLatLngs(ends)
        it.casing.setStyle({ weight: weight + 2.6, opacity: 0.8 })
        it.line.setLatLngs(ends)
        it.line.setStyle({ weight, opacity: 0.95 })
      }
    }
    restyle()
    const offZoom = onZoomChange(map, restyle)
    return () => {
      offZoom()
      lineGroup.remove()
      casingGroup.remove()
    }
  }, [map, links, linkTypeLabel, isDark])

  /* ---------- 外部定位（搜索结果 / 详情按钮） ---------- */
  useEffect(() => {
    if (!focus || !isMapAlive(map)) return
    const site = sites.find((s) => s.index === focus.index && s.located)
    if (!site) return
    const zoom = focus.zoom ?? Math.max(map.getZoom(), 11)
    map.flyTo([site.lat as number, site.lng as number], Math.min(zoom, 13), { duration: 0.7 })
    // 只在 token 变化时飞一次；selected 同步更新不重复触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.token])

  return null
}
