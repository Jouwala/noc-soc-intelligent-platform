/* ============================================================================
 * location/LocationMapCard —— 位置页的地图卡（地图 + 两个搜索窗）
 * ============================================================================
 * 从 pages/LocationMap.tsx 拆出（该页 537 行，超过 AGENTS 三.2 的 500 行上限）。
 * 卡片里只有三件事：
 *   · 地图画布（LocationMapView）
 *   · 右上角「查询订阅者」手机号搜索窗
 *   · 右下角「按经纬度定位」坐标搜索窗
 * 取数、选中态、抽屉仍由页面持有；本组件只接收 props 并回调。
 *
 * 两个浮层都必须放在显式的 position:relative 容器里（不能用 Card body 自身）：
 * antd 版本差异下 body 的定位会被忽略，浮层会相对整张卡定位、盖住卡头标题。
 * ========================================================================== */
import { Spin, Typography } from 'antd'

import { useI18n } from '../../i18n'
import { useIsMobile } from '../../theme'
import { CardBase } from '../../components/cards'
import type { Site, TopologyLink } from '../gismap/types'
import { fmtLatLon } from './geo'
import { LocationMapView, type RingSite, type TrackCell } from './LocationMapView'
import SubscriberSearchOverlay from './SubscriberSearchOverlay'
import CoordinateSearchOverlay from './CoordinateSearchOverlay'
import type { FiberLink } from './useLocationData'
import type { VmaxRange } from '../vmaxWindow'

const { Text } = Typography

export default function LocationMapCard({
  center, radiusKm, ringSites, mwLinks, fiberLinks, nearestMw, nearestFiber,
  trackCells, fitToken, loadingSites, onSite, onMw, onFiber, onTrack,
  sub, coord,
}: {
  center: { lat: number; lon: number }
  radiusKm: number
  ringSites: RingSite[]
  mwLinks: TopologyLink[]
  fiberLinks: FiberLink[]
  nearestMw: TopologyLink | null
  nearestFiber: FiberLink | null
  trackCells: TrackCell[]
  fitToken: number
  /** 首次站点加载中（还没有任何站点）时先显示转圈，避免空地图闪一下 */
  loadingSites: boolean
  onSite: (site: Site) => void
  onMw: (link: TopologyLink) => void
  onFiber: (link: FiberLink) => void
  onTrack: () => void
  sub: {
    msisdn: string
    onMsisdnChange: (v: string) => void
    onSearch: () => void
    loading: boolean
    diagLoading: boolean
    searched: boolean
    resultMsisdn?: string
    cellCount: number
    missing: number
    hasResult: boolean
    onOpen: () => void
    range: VmaxRange
    onRangeChange: (r: VmaxRange) => void
  }
  coord: {
    latText: string
    lonText: string
    onLatText: (v: string) => void
    onLonText: (v: string) => void
    onApply: () => void
    error: string
  }
}) {
  const { t } = useI18n()
  const isMobile = useIsMobile()

  return (
    <CardBase
      title={<span>{t('loc.map.title')}</span>}
      extra={
        <Text type="secondary" style={{ fontSize: 12 }}>
          {isMobile
            /* 窄屏只放短标签，长文本会把「位置地图」标题挤成 0 宽（390px 实测） */
            ? t('loc.map.offlineShort')
            : (
              <>
                {fmtLatLon(center)} · {t('loc.map.radiusPlain', { r: radiusKm })}
                {' · '}{t('loc.map.offline')}
                {trackCells.length > 0 ? ` · ${t('loc.map.trackShown', { n: trackCells.length })}` : ''}
              </>
            )}
        </Text>
      }
      style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
      bodyStyle={{ padding: 0, flex: '1 1 0%', minHeight: 520, display: 'flex' }}
    >
      <div style={{ position: 'relative', flex: 1, minHeight: 520 }}>
        {loadingSites ? (
          <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Spin />
          </div>
        ) : (
          <LocationMapView
            center={center}
            radiusKm={radiusKm}
            ringSites={ringSites}
            mwLinks={mwLinks}
            fiberLinks={fiberLinks}
            nearestMw={nearestMw}
            nearestFiber={nearestFiber}
            trackCells={trackCells}
            fitToken={fitToken}
            onSite={onSite}
            onMw={onMw}
            onFiber={onFiber}
            onTrack={onTrack}
          />
        )}

        {/* 手机号搜索窗：右上角 */}
        <SubscriberSearchOverlay
          msisdn={sub.msisdn}
          onMsisdnChange={sub.onMsisdnChange}
          onSearch={sub.onSearch}
          loading={sub.loading}
          diagLoading={sub.diagLoading}
          searched={sub.searched}
          resultMsisdn={sub.resultMsisdn}
          cellCount={sub.cellCount}
          missing={sub.missing}
          hasResult={sub.hasResult}
          onOpen={sub.onOpen}
          range={sub.range}
          onRangeChange={sub.onRangeChange}
        />

        {/* 经纬度搜索窗：右下角。输入后点「定位」即应用；
            底部控制条里的「应用坐标」与它共用同一份 state。 */}
        <CoordinateSearchOverlay
          latText={coord.latText}
          lonText={coord.lonText}
          onLatText={coord.onLatText}
          onLonText={coord.onLonText}
          onApply={coord.onApply}
          error={coord.error}
        />
      </div>
    </CardBase>
  )
}
