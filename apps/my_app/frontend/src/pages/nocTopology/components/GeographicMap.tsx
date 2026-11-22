/* ============================================================================
 * NOC 拓扑中心 —— 地理视图（Leaflet + 自托管 PMTiles）
 * ============================================================================
 * 复用项目既有能力，不引入新地图依赖、不访问公网瓦片：
 *   `components/LeafletMap`（MapContainer）+ `pages/gismap/PmtilesLayer`（离线底图）
 *   + `pages/networkTopologyV2/CountyLayer`（县界）
 *
 * 两个模式：
 *   · overview —— 按来源着色，看三个网络域在地图上的分布；
 *   · geo      —— 按状态着色，链路按状态着色（有流量的链路才谈得上负载）。
 *
 * 性能：三个来源实测 1100+ 点、1100+ 线。这里不用 SVG 渲染器，改成
 * `L.canvas()` 画点/线；选中高亮单独一层，点选不会重建整层几何。
 *
 * ⚠ canvas 渲染器**解析不了 CSS 变量**（与 ECharts 同理），所以颜色在这里
 *   由 `cssColor()` 从 `--wh-*` 读成实值再交给 Leaflet；主题切换时重算。
 * ========================================================================== */
import { useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'

import { MapContainer, isMapAlive, useMap } from '../../../components/LeafletMap'
import api from '../../../api'
import { cachedGet } from '../../../cache'
import { useTheme } from '../../../theme'
import { BASEMAP_MAX_ZOOM, BASEMAP_PMTILES_URL } from '../../gismap/basemap'
import { PmtilesLayer } from '../../gismap/PmtilesLayer'
import CountyLayer, { type CountyPolygon } from '../../networkTopologyV2/CountyLayer'
import { hasGeo } from '../model'
import type { NocEdge, NocNode, NocStatus } from '../types'

export const LB_CENTER: [number, number] = [6.35, -9.5]
export const LB_ZOOM = 7

export interface GeographicMapProps {
  nodes: NocNode[]
  edges: NocEdge[]
  mode: 'overview' | 'geo'
  selectedNode: string | null
  onSelectNode: (key: string) => void
  resetToken: number
  locate: { key: string; token: number } | null
}

interface Palette {
  ok: string
  warn: string
  major: string
  danger: string
  teal: string
  blue: string
  dim: string
}

/** canvas 用实值：CSS 变量在这里不生效（见文件头） */
function cssColor(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

function readPalette(): Palette {
  return {
    ok: cssColor('--wh-ok', '#0bc78c'),
    warn: cssColor('--wh-warn', '#f6cf00'),
    major: cssColor('--wh-sev-major', '#ff922b'),
    danger: cssColor('--wh-danger', '#ff5c6c'),
    teal: cssColor('--wh-teal', '#00d9ff'),
    blue: cssColor('--wh-blue', '#258cff'),
    dim: cssColor('--wh-text-dim', '#6f8cb4'),
  }
}

function statusInk(status: NocStatus, p: Palette): string {
  switch (status) {
    case 'ok': return p.ok
    case 'minor': return p.warn
    case 'major': return p.major
    case 'critical': return p.danger
    case 'down': return p.danger
    default: return p.dim
  }
}

function sourceInk(source: NocNode['source'], p: Palette): string {
  switch (source) {
    case 'ipran': return p.blue
    case 'mw': return p.warn
    default: return p.teal
  }
}

function radiusOf(n: NocNode): number {
  switch (n.deviceType) {
    case 'core': return 7
    case 'aggregation': return 6
    case 'isp': return 6
    case 'microwave': return 5
    case 'access': return 4.5
    default: return 4
  }
}

function MapLimits() {
  const map = useMap()
  useEffect(() => {
    map.setMinZoom(6)
    map.setMaxZoom(BASEMAP_MAX_ZOOM)
  }, [map])
  return null
}

/** 回全景：按当前可见节点拟合，而不是固定 zoom —— 只看 MW 时不该留一屏空白。
 *  nodes 走 ref，避免每次数据刷新都把用户的平移/缩放拽回去。 */
function MapHome({ token, nodes }: { token: number; nodes: NocNode[] }) {
  const map = useMap()
  const nodesRef = useRef(nodes)
  nodesRef.current = nodes
  useEffect(() => {
    if (!token) return
    const pts = nodesRef.current.filter(hasGeo)
    if (pts.length < 2) { map.setView(LB_CENTER, LB_ZOOM); return }
    const bounds = L.latLngBounds(pts.map((n) => [n.latitude as number, n.longitude as number] as [number, number]))
    map.fitBounds(bounds, { padding: [24, 24], maxZoom: 9 })
  }, [token, map])
  return null
}

/** 一张地图只用一个 canvas 渲染器：每次点选都 new 一个会在地图里留下
 *  越来越多的空 canvas 元素（Leaflet 不会因为图层被移除就回收渲染器）。 */
function canvasRenderer(map: L.Map): L.Canvas {
  const holder = map as unknown as { _nocCanvas?: L.Canvas }
  if (!holder._nocCanvas) holder._nocCanvas = L.canvas({ padding: 0.4 })
  return holder._nocCanvas
}

/** 点/线几何层（canvas 渲染器，只在数据/模式/主题变化时重建） */
function NocGeoLayer({
  nodes, edges, mode, palette, onSelect,
}: {
  nodes: NocNode[]
  edges: NocEdge[]
  mode: 'overview' | 'geo'
  palette: Palette
  onSelect: (key: string) => void
}) {
  const map = useMap()

  useEffect(() => {
    if (!isMapAlive(map)) return
    const renderer = canvasRenderer(map)
    const group = L.layerGroup().addTo(map)
    const byKey = new Map(nodes.map((n) => [n.key, n]))

    // 线先画（压在点下面）
    for (const e of edges) {
      const a = byKey.get(e.sourceNode)
      const b = byKey.get(e.targetNode)
      if (!a || !b || !hasGeo(a) || !hasGeo(b)) continue
      const color = mode === 'overview'
        ? (e.medium === 'microwave' ? palette.warn : palette.teal)
        : statusInk(e.status, palette)
      group.addLayer(L.polyline(
        [[a.latitude as number, a.longitude as number], [b.latitude as number, b.longitude as number]],
        {
          renderer,
          color,
          weight: e.medium === 'microwave' ? 1.4 : 1,
          opacity: mode === 'overview' ? 0.3 : 0.5,
          dashArray: e.planned ? '4 4' : (e.schematic ? '2 4' : undefined),
          interactive: false,
        },
      ))
    }

    for (const n of nodes) {
      if (!hasGeo(n)) continue
      const fill = mode === 'overview' ? sourceInk(n.source, palette) : statusInk(n.status, palette)
      const m = L.circleMarker([n.latitude as number, n.longitude as number], {
        renderer,
        radius: radiusOf(n),
        fillColor: fill,
        fillOpacity: 0.9,
        color: mode === 'overview' ? palette.teal : sourceInk(n.source, palette),
        weight: 1,
        opacity: 0.85,
      })
      m.bindTooltip(`${n.name}${n.ip ? ` · ${n.ip}` : ''}`, { direction: 'top', sticky: true })
      m.on('click', () => onSelect(n.key))
      group.addLayer(m)
    }

    return () => { group.remove() }
  }, [map, nodes, edges, mode, palette, onSelect])

  return null
}

/** 选中高亮：单独一层，点选不重建几何 */
function SelectionRing({
  nodes, selectedNode, palette,
}: {
  nodes: NocNode[]
  selectedNode: string | null
  palette: Palette
}) {
  const map = useMap()
  useEffect(() => {
    if (!isMapAlive(map) || !selectedNode) return
    const n = nodes.find((x) => x.key === selectedNode)
    if (!n || !hasGeo(n)) return
    const ring = L.circleMarker([n.latitude as number, n.longitude as number], {
      renderer: canvasRenderer(map),
      radius: radiusOf(n) + 6,
      fill: false,
      color: palette.teal,
      weight: 2,
      opacity: 1,
    }).addTo(map)
    return () => { ring.remove() }
  }, [map, nodes, selectedNode, palette])
  return null
}

/** 搜索定位：独立组件，只认 token，不参与几何层重建 */
function GeoFly({ target }: { target: { lat: number; lon: number; token: number } | null }) {
  const map = useMap()
  useEffect(() => {
    if (!target) return
    map.flyTo([target.lat, target.lon], Math.max(map.getZoom(), 10), { duration: 0.5 })
    // 只认 token：同一节点再次定位也要飞过去
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.token])
  return null
}

export default function GeographicMap({
  nodes, edges, mode, selectedNode, onSelectNode, resetToken, locate,
}: GeographicMapProps) {
  const { isDark } = useTheme()
  const [counties, setCounties] = useState<CountyPolygon[]>([])
  // isDark 依赖：主题切换后重新读一次实值
  const palette = useMemo(() => readPalette(), [isDark])

  useEffect(() => {
    let alive = true
    cachedGet<{ counties?: CountyPolygon[] }>(
      'gis:county-boundaries',
      () => api.get('/gis/county-boundaries'),
      30 * 60 * 1000,
    )
      .then((res) => {
        if (alive) setCounties(Array.isArray(res.data?.counties) ? res.data.counties : [])
      })
      .catch(() => { /* 县界只是底衬，失败不影响主体 */ })
    return () => { alive = false }
  }, [])

  const focusTarget = useMemo(() => {
    if (!locate) return null
    const n = nodes.find((x) => x.key === locate.key)
    if (!n || !hasGeo(n)) return null
    return { lat: n.latitude as number, lon: n.longitude as number, token: locate.token }
  }, [locate, nodes])

  return (
    <MapContainer
      center={LB_CENTER}
      zoom={LB_ZOOM}
      className={`nocgeo-map${isDark ? ' is-dark' : ''}`}
      style={{ position: 'absolute', inset: 0, background: 'var(--wh-page-bg)' }}
    >
      <PmtilesLayer
        url={BASEMAP_PMTILES_URL}
        isDark={isDark}
        maxZoom={BASEMAP_MAX_ZOOM}
        showLabels={false}
        showRoads={false}
      />
      <MapLimits />
      <MapHome token={resetToken} nodes={nodes} />
      <GeoFly target={focusTarget} />
      <CountyLayer counties={counties} isDark={isDark} />
      <NocGeoLayer nodes={nodes} edges={edges} mode={mode} palette={palette} onSelect={onSelectNode} />
      <SelectionRing nodes={nodes} selectedNode={selectedNode} palette={palette} />
    </MapContainer>
  )
}
