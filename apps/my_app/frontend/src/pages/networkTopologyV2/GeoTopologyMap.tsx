/* ============================================================================
 * OLIB GIS 拓扑 —— 地图容器
 * ============================================================================
 * 复用项目既有 Leaflet 封装 + 自托管 PMTiles 底图（与 ?page=gis 同一套技术），
 * 不引入任何新的地图依赖、不访问公网瓦片。
 *
 * 图层自下而上：PMTiles 底图（自托管）→ 县界 → 骨干链路示意线 → 站点/聚合。
 * ========================================================================== */
import { useEffect, useState } from 'react'
import { MapContainer, useMap } from '../../components/LeafletMap'
import api from '../../api'
import { cachedGet } from '../../cache'
import {
  BASEMAP_MAX_ZOOM, BASEMAP_PMTILES_URL,
} from '../gismap/basemap'
import { PmtilesLayer } from '../gismap/PmtilesLayer'
import CountyLayer, { type CountyPolygon } from './CountyLayer'
import GeoTopologyLayer from './GeoTopologyLayer'
import type { CoreLinkType } from '../networkTopology/topologyData'
import type { FiberLink, GeoLink, GeoSite } from './types'

export const LB_CENTER: [number, number] = [6.35, -9.5]
export const LB_ZOOM = 8

function MapLimits() {
  const map = useMap()
  useEffect(() => {
    map.setMinZoom(6)
    map.setMaxZoom(BASEMAP_MAX_ZOOM)
  }, [map])
  return null
}

function MapReset({ token }: { token: number }) {
  const map = useMap()
  useEffect(() => {
    if (!token) return
    map.setView(LB_CENTER, LB_ZOOM)
  }, [token, map])
  return null
}

export interface GeoTopologyMapProps {
  sites: GeoSite[]
  links: GeoLink[]
  fiberLinks: FiberLink[]
  selected: GeoSite | null
  focus: { index: number; token: number; zoom?: number } | null
  resetToken: number
  isDark: boolean
  onSelect: (index: number) => void
  typeLabel: (site: GeoSite) => string
  clusterLabel: (count: number) => string
  linkTypeLabel: (type: CoreLinkType) => string
}

export default function GeoTopologyMap({
  sites, links, fiberLinks, selected, focus, resetToken, isDark, onSelect,
  typeLabel, clusterLabel, linkTypeLabel,
}: GeoTopologyMapProps) {
  const [counties, setCounties] = useState<CountyPolygon[]>([])

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
      .catch((err: unknown) => {
        /* 县界只是底衬：失败不影响站点/链路，不弹错；留一条 warn 便于排查。 */
        console.warn('[olibV2] 县界加载失败，地图主体继续渲染', err)
      })
    return () => { alive = false }
  }, [])

  return (
    <MapContainer
      center={LB_CENTER}
      zoom={LB_ZOOM}
      className={`olibv2-map${isDark ? ' is-dark' : ''}`}
      style={{ position: 'absolute', inset: 0, background: 'var(--wh-page-bg)' }}
    >
      <PmtilesLayer
        url={BASEMAP_PMTILES_URL}
        isDark={isDark}
        maxZoom={BASEMAP_MAX_ZOOM}
        showLabels={false}
        showRoads
      />
      <MapLimits />
      <MapReset token={resetToken} />
      <CountyLayer counties={counties} isDark={isDark} />
      <GeoTopologyLayer
        sites={sites}
        links={links}
        fiberLinks={fiberLinks}
        selected={selected}
        focus={focus}
        onSelect={onSelect}
        typeLabel={typeLabel}
        clusterLabel={clusterLabel}
        linkTypeLabel={linkTypeLabel}
        isDark={isDark}
      />
    </MapContainer>
  )
}
