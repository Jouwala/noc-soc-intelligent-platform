/* ============================================================================
 * location/LocationMapView —— 位置页地图画布（只画，不管取数与抽屉）
 * ============================================================================
 *
 * 图层（由下到上）：
 *   1. PMTiles 矢量底图（与 GIS 页同一份自托管瓦片，道路关、地名关）
 *   2. 半径虚线圈（用户选的搜索半径）
 *   3. 光纤链路（OLIB PDF 站点对；最近一条加粗）
 *   4. MW 链路（GIS 拓扑 + 15 分钟利用率；最近一条加粗，颜色与 GIS 同一色阶）
 *   5. 最近一圈站点（颜色走 gismap/types 的 siteLegendKey → 与 GIS 完全一致）
 *   6. 订阅者轨迹小区（编号圆点 + 虚线路径）
 *   7. 位置点（红十字圆心）
 *
 * 交互：点站点 / 链路 / 轨迹点都交给页面处理（打开对应抽屉），本组件不持有
 * 业务状态；「全览」由页面把 fitToken 加一触发。
 * ========================================================================== */
import { useEffect, useRef, useState } from 'react'
import { CircleMarker, MapContainer, Marker, Polygon, Polyline, Tooltip, useMap } from '../../components/LeafletMap'
import { useI18n } from '../../i18n'
import { useIsMobile, useTheme } from '../../theme'
import { PmtilesLayer } from '../gismap/PmtilesLayer'
import { LayerToggleControl } from '../gismap/LayerToggleControl'
import { BASEMAP_MAX_ZOOM, BASEMAP_PMTILES_URL } from '../gismap/basemap'
import type { Site, TopologyLink } from '../gismap/types'
import { UTIL_LEVEL_COLORS, colorFor, utilLevelOf } from '../gismap/types'
import { circlePolygon, fmtKm, type LatLon } from './geo'
import { fmtSeen } from './trackTime'
import type { FiberLink } from './useLocationData'
import { siteColor } from './siteColors'

export type RingSite = { site: Site; km: number }
export type TrackCell = {
  key: string
  name: string
  lat: number
  lon: number
  vendor?: string
  rat?: number | string
  /** 该小区在 VMAX 诊断记录里最后一次出现的时间（可能为空） */
  lastSeen?: string
  /** 命中记录条数（悬浮说明用） */
  hits?: number
}

const FIBER_COLOR = '#b197fc'

function CenterController({
  center, fitToken, bounds, trackCells,
}: {
  center: LatLon
  fitToken: number
  bounds: [number, number][]
  trackCells: TrackCell[]
}) {
  const map = useMap()
  useEffect(() => {
    map.setView([center.lat, center.lon], Math.max(9, Math.min(14, map.getZoom())), { animate: false })
    // 只在位置点变化时重置视野；zoom 变化交给用户
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [center.lat, center.lon, map])

  useEffect(() => {
    if (fitToken <= 0 || !bounds.length) return
    map.fitBounds(bounds as any, { padding: [36, 36], maxZoom: 13 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitToken, map])

  useEffect(() => {
    // 容器尺寸在 flex 布局里可能晚一拍确定；不 invalidate 会半屏灰
    const t = window.setTimeout(() => map.invalidateSize(), 80)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map])

  /* 订阅者轨迹到手后**自动把视野放到轨迹上**（2026-11-23 用户要求：
     「Subscriber track 应该放到地图上」）。此前只把点画出来、地图仍停在
     位置点那圈；用户输了号码却看不到点，尤其号码在别的城市时。
     · 用「小区 key 串」判断是否换了一批结果：同一批不重复 fit（否则用户
       每次开抽屉/重渲染都会被拽回原位）；
     · 只 fit 轨迹本身（不是全图层），单个小区时 setView 到 14 级；
     · 用户之后手动拖动/缩放不受影响，直到下一次查询返回不同的小区集合。 */
  const trackSig = trackCells.map((c) => c.key).join('|')
  const fittedRef = useRef('')
  useEffect(() => {
    if (!trackSig) { fittedRef.current = ''; return }
    if (fittedRef.current === trackSig) return
    fittedRef.current = trackSig
    const pts: [number, number][] = trackCells.map((c) => [c.lat, c.lon])
    if (!pts.length) return
    if (pts.length === 1) map.setView(pts[0], Math.max(14, map.getZoom()), { animate: false })
    else map.fitBounds(pts as any, { padding: [60, 60], maxZoom: 14 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trackSig, map])
  return null
}

/** 人形符号（最后一个驻留小区）。内联样式/SVG：不依赖额外图片与全局 CSS。 */
function personIconHtml(isDark: boolean): string {
  const bg = isDark ? '#ffd43b' : '#f59e0b'
  return `<div style="width:32px;height:32px;border-radius:50%;background:${bg};border:2px solid #fff;`
    + 'box-shadow:0 0 0 3px rgba(255,59,48,.55),0 2px 8px rgba(0,0,0,.35);'
    + 'display:flex;align-items:center;justify-content:center">'
    + '<svg width="18" height="18" viewBox="0 0 24 24" fill="#111827">'
    + '<circle cx="12" cy="7" r="3.4"/><path d="M4.8 21c0-4.3 3.2-7.2 7.2-7.2s7.2 2.9 7.2 7.2H4.8z"/>'
    + '</svg></div>'
}

export function LocationMapView({
  center, radiusKm, ringSites, mwLinks, fiberLinks, nearestMw, nearestFiber,
  trackCells, fitToken, onSite, onMw, onFiber, onTrack,
}: {
  center: LatLon
  radiusKm: number
  ringSites: RingSite[]
  mwLinks: TopologyLink[]
  fiberLinks: FiberLink[]
  nearestMw: TopologyLink | null
  nearestFiber: FiberLink | null
  trackCells: TrackCell[]
  fitToken: number
  onSite: (site: Site) => void
  onMw: (link: TopologyLink) => void
  onFiber: (link: FiberLink) => void
  onTrack: (cell: TrackCell) => void
}) {
  const { t } = useI18n()
  const { isDark } = useTheme()
  const isMobile = useIsMobile()
  /* 底图开关与 ?page=gis 一致：**默认全关**。瓦片自带 1.7 万个地名，全开会糊住
     站点；道路全开也会让离线矢量图看起来像「在线 OSM」。需要时由用户在这里开。
     2026-11-23 用户问「为什么没用项目的离线地图」：底图本来就是本机 PMTiles，
     只是此前固定开了道路、视觉上像在线底图；现改回与 GIS 同档并给出开关。 */
  const [showLabels, setShowLabels] = useState(false)
  const [showRoads, setShowRoads] = useState(false)

  const bounds: [number, number][] = [
    [center.lat, center.lon],
    ...ringSites.map((r) => [r.site.lat, r.site.lng] as [number, number]),
    ...fiberLinks.flatMap((f) => [
      [f.aLat, f.aLon] as [number, number],
      [f.bLat, f.bLon] as [number, number],
    ]),
    ...trackCells.map((c) => [c.lat, c.lon] as [number, number]),
  ]

  return (
    <div style={{ position: 'relative', height: '100%', minHeight: 420 }}>
      <MapContainer
        center={[center.lat, center.lon]}
        zoom={11}
        style={{ position: 'absolute', inset: 0, width: 'auto', height: 'auto' }}
      >
        <PmtilesLayer
          url={BASEMAP_PMTILES_URL}
          isDark={isDark}
          maxZoom={BASEMAP_MAX_ZOOM}
          showLabels={showLabels}
          showRoads={showRoads}
        />
        {/* 底图注记 / 道路开关：复用 GIS 页同一个 LAYERS 浮层，文案也复用 gis.layer* 键。
            必须放在 <MapContainer> 内（组件内部 useMap() 取容器做 portal 挂载点）。 */}
        <LayerToggleControl
          title={t('gis.layerTitle')}
          items={[
            {
              key: 'labels',
              label: t('gis.layerLabels'),
              hint: t('gis.layerLabelsHint'),
              checked: showLabels,
              onChange: setShowLabels,
            },
            {
              key: 'roads',
              label: t('gis.layerRoads'),
              hint: t('gis.layerRoadsHint'),
              checked: showRoads,
              onChange: setShowRoads,
            },
          ]}
        />
        <CenterController center={center} fitToken={fitToken} bounds={bounds} trackCells={trackCells} />

        {/* 搜索半径 */}
        <Polygon
          positions={circlePolygon(center, radiusKm)}
          pathOptions={{
            color: isDark ? '#00d9ff' : '#0e7490',
            weight: 1.4,
            opacity: 0.85,
            fill: false,
            dashArray: '6 6',
          }}
        />

        {/* 光纤链路（OLIB 站点对，示意图） */}
        {fiberLinks.map((f) => {
          const isNearest = nearestFiber?.id === f.id
          return (
            <Polyline
              key={f.id}
              positions={[[f.aLat, f.aLon], [f.bLat, f.bLon]]}
              pathOptions={{
                color: FIBER_COLOR,
                weight: isNearest ? 3.4 : 1.2,
                opacity: isNearest ? 0.95 : 0.45,
                dashArray: isNearest ? undefined : '5 6',
              }}
              onClick={() => onFiber(f)}
            >
              <Tooltip>
                <span style={{ fontSize: 12 }}>
                  {t('loc.fo.title')} · {f.aName} ↔ {f.bName}
                  {f.type ? ` · ${f.type}` : ''}
                </span>
              </Tooltip>
            </Polyline>
          )
        })}

        {/* MW 链路（GIS 拓扑 + 利用率色阶） */}
        {mwLinks.map((l) => {
          const isNearest = nearestMw?.name === l.name
          const level = l.level ?? utilLevelOf(l.util)
          const color = UTIL_LEVEL_COLORS[level] || '#6f8cb4'
          return (
            <Polyline
              key={`${l.name}-${l.site_a_name}-${l.site_b_name}`}
              positions={[[l.lat_a, l.lng_a], [l.lat_b, l.lng_b]]}
              pathOptions={{
                color,
                weight: isNearest ? 4 : 1.5,
                opacity: isNearest ? 1 : 0.4,
              }}
              onClick={() => onMw(l)}
            >
              <Tooltip>
                <span style={{ fontSize: 12 }}>
                  {t('loc.mw.title')} · {l.name || `${l.site_a_name}-${l.site_b_name}`}
                  {l.util == null ? ` · ${t('loc.list.noKpi')}` : ` · ${Number(l.util).toFixed(2)}%`}
                </span>
              </Tooltip>
            </Polyline>
          )
        })}

        {/* 最近一圈站点：颜色规则与 GIS 完全同源 */}
        {ringSites.map(({ site, km }) => (
          <CircleMarker
            key={site.site_id}
            center={[site.lat, site.lng]}
            radius={site.site_id === ringSites[0]?.site.site_id ? 8 : 6.5}
            fillColor={siteColor(site)}
            color="#ffffff"
            weight={1.6}
            opacity={0.95}
            fillOpacity={1}
            onClick={() => onSite(site)}
          >
            <Tooltip>
              <span style={{ fontSize: 12 }}>
                <b>{site.site_name}</b> · {fmtKm(km)}
                <br />
                {[site.province_county, site.region].filter(Boolean).join(' / ')}
                <br />
                {t('loc.site.alarms')}: {site.rt_alarm_count} RT / {site.his_alarm_count} His
              </span>
            </Tooltip>
          </CircleMarker>
        ))}

        {/* 订阅者驻留小区（2026-11-23 用户要求放到地图上）：
            CCA 的 usertrack 没有时间字段，返回顺序也不保证时间序，所以
            **只画点、不画连线**（连线会被读成「用户按这个顺序走过」，是错的）。
            最后一个有坐标的小区用**人形符号**标出，即用户最近一次驻留的小区。 */}
        {trackCells.map((c, i) => {
          /* CCA usertrack 是**倒序**（用户 2026-11-23 确认）：第 1 条即最新，
             所以人形符号画在 i === 0，而不是最后一条。 */
          const isLatest = i === 0
          if (!isLatest) {
            return (
              <CircleMarker
                key={c.key}
                center={[c.lat, c.lon]}
                radius={6.5}
                fillColor={isDark ? '#ffd43b' : '#b45309'}
                color="#ffffff"
                weight={1.6}
                opacity={0.95}
                fillOpacity={0.95}
                onClick={() => onTrack(c)}
              >
                <Tooltip>
                  <span style={{ fontSize: 12 }}>
                    #{i + 1} {c.name}{c.vendor ? ` · ${c.vendor}` : ''}
                    {c.lastSeen ? ` · ${fmtSeen(c.lastSeen)}` : ''}
                  </span>
                </Tooltip>
              </CircleMarker>
            )
          }
          return (
            <Marker
              key={c.key}
              position={[c.lat, c.lon]}
              html={personIconHtml(isDark)}
              iconSize={[32, 32]}
              iconAnchor={[16, 16]}
              iconKey="track-last"
              className="loc-track-person"
              zIndexOffset={900}
              onClick={() => onTrack(c)}
            >
              <Tooltip direction="top">
                <span style={{ fontSize: 12 }}>
                  <b>{t('loc.map.trackLast')}</b>（#1）· {c.name}{c.vendor ? ` · ${c.vendor}` : ''}
                  {c.lastSeen ? ` · ${fmtSeen(c.lastSeen)}` : ''}
                </span>
              </Tooltip>
            </Marker>
          )
        })}

        {/* 位置点 */}
        <CircleMarker
          center={[center.lat, center.lon]}
          radius={9}
          fillColor="#ff3b30"
          color="#ffffff"
          weight={2.4}
          opacity={1}
          fillOpacity={1}
        >
          <Tooltip permanent direction="top">
            <span style={{ fontSize: 12 }}>{t('loc.map.here')}</span>
          </Tooltip>
        </CircleMarker>
      </MapContainer>

      {/* 图例：窄屏隐藏 —— 390px 下它会与「按经纬度定位」搜索窗互相遮挡，
          且地图本身只剩很窄一条；图例信息在桌面端保留。 */}
      {!isMobile && (
        <div
          style={{
            position: 'absolute', left: 10, bottom: 10, zIndex: 500,
            background: isDark ? 'rgba(5,13,34,0.82)' : 'rgba(255,255,255,0.9)',
            border: `1px solid ${isDark ? 'rgba(0,217,255,0.28)' : 'rgba(13,148,136,0.28)'}`,
            borderRadius: 8, padding: '7px 9px', fontSize: 11, lineHeight: 1.7,
            pointerEvents: 'none', maxWidth: 220,
          }}
        >
          <div style={{ fontWeight: 600 }}>{t('loc.map.legend')}</div>
          {([
            ['rt_alarm', t('loc.map.legend.rt')],
            ['his_alarm', t('loc.map.legend.his')],
            ['vip', t('loc.map.legend.vip')],
            ['normal', t('loc.map.legend.normal')],
          ] as const).map(([key, label]) => (
            <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 9, height: 9, borderRadius: '50%', background: colorFor(key), display: 'inline-block' }} />
              {label}
            </div>
          ))}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 14, height: 2, background: UTIL_LEVEL_COLORS.critical, display: 'inline-block' }} />
            {t('loc.map.legend.mw')}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 14, height: 0, borderTop: `2px dashed ${FIBER_COLOR}`, display: 'inline-block' }} />
            {t('loc.map.legend.fiber')}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 9, height: 9, borderRadius: '50%', background: isDark ? '#ffd43b' : '#b45309', display: 'inline-block' }} />
            {t('loc.map.legend.track')}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{
              width: 13, height: 13, borderRadius: '50%', background: isDark ? '#ffd43b' : '#f59e0b',
              border: '1.5px solid #fff', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <svg width="7" height="7" viewBox="0 0 24 24" fill="#111827">
                <circle cx="12" cy="7" r="3.4" /><path d="M4.8 21c0-4.3 3.2-7.2 7.2-7.2s7.2 2.9 7.2 7.2H4.8z" />
              </svg>
            </span>
            {t('loc.map.legend.trackLast')}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 9, height: 9, borderRadius: '50%', background: '#ff3b30', display: 'inline-block' }} />
            {t('loc.map.legend.here')}
          </div>
        </div>
      )}
    </div>
  )
}
