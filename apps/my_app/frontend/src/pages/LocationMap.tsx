/* ============================================================================
 * LocationMap —— 位置页（?page=location）
 * ============================================================================
 * 以经纬度为中心：最近一圈站点、最近 MW/光纤链路、订阅者轨迹与 VMAX 诊断。
 * 站点配色/抽屉与 ?page=gis 同源，MW 明细与 ?page=mwRealTime 对齐。
 * 经纬度入口：URL、手动、浏览器定位、问答助手（/api/portal-qa/ask）。
 * 口径与交互细节见 docs/manual/11-feature-guide.md 的 11b 节。
 *
 * 门户 GIS Map 的 Location 页签用 `?maponly=1`：只渲染地图画布（100vh），
 * 不渲染列表/控制条/轨迹表/标题卡；见下方 mapOnly 分支。
 * ========================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Col, Row } from 'antd'

import api from '../api'
import { useI18n } from '../i18n'
import { pageWrap } from '../theme'
import { CardBase } from '../components/cards'
import { ExplainIcon } from '../components/MetricExplain'
import { readParam, writeParams } from '../urlState'
import type { DrilldownAnalytics, DrilldownData, Site, TopologyLink } from './gismap/types'
import { SiteDrilldownDrawer } from './gismap/SiteDrilldownDrawer'
import { useVmaxDiagnosis, LEGACY_NS } from './useVmaxDiagnosis'
import { SitePanel, LinksPanel, SubscriberPanel } from './location/LocationPanels'
import LocationMapCard from './location/LocationMapCard'
import LocationIntroCard from './location/LocationIntroCard'
import type { TrackCell } from './location/LocationMapView'
import { buildTrackTimes, timeOfCell } from './location/trackTime'
import LocationControls from './location/LocationControls'
import MwLinkDrawer from './location/MwLinkDrawer'
import FiberLinkDrawer from './location/FiberLinkDrawer'
import SubscriberDrawer from './location/SubscriberDrawer'
import { useLocationData, type FiberLink } from './location/useLocationData'
import { siteFromAnswer } from './location/answerSite'
import {
  distanceToSegmentKm, fmtKm, fmtLatLon, haversineKm, parseLatLon, ringOf, type LatLon,
} from './location/geo'

/** 默认位置：蒙罗维亚市区。没有 URL 参数时给一个「一打开就有一圈站点」的起点。 */
const DEFAULT_LOCATION: LatLon = { lat: 6.3, lon: -10.795 }

/** 各类链路的候选条数（地图上画几条；最近的那条恒在第一位） */
const LINK_DRAW_COUNT = 6

const cellKeyOf = (c: { cell?: unknown; cellkey?: unknown; cellname?: unknown }, i: number): string =>
  String(c.cell ?? c.cellkey ?? c.cellname ?? i)

const isValidLatLon = (lat: number, lon: number): boolean =>
  Number.isFinite(lat) && Number.isFinite(lon)
  && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180
  && !(lat === 0 && lon === 0)

export default function LocationMap() {
  const { t, locale } = useI18n()
  const data = useLocationData()
  const sub = useVmaxDiagnosis(LEGACY_NS)
  /* 门户地图页签：?maponly=1 只留地图画布（与 Person/olibTopo 同一约定） */
  const [mapOnly] = useState(() => {
    const v = new URLSearchParams(window.location.search).get('maponly')
    return v === '1' || v === 'true'
  })

  /* ---------------- 位置与半径 ---------------- */
  const initial = useMemo<LatLon>(() => {
    const lat = Number(readParam('lat'))
    const lon = Number(readParam('lon'))
    return isValidLatLon(lat, lon) ? { lat, lon } : DEFAULT_LOCATION
  }, [])
  const [location, setLocation] = useState<LatLon>(initial)
  const [latText, setLatText] = useState(initial.lat.toFixed(6))
  const [lonText, setLonText] = useState(initial.lon.toFixed(6))
  const [locError, setLocError] = useState('')
  const [geoBusy, setGeoBusy] = useState(false)
  const [radiusKm, setRadiusKm] = useState(15)
  const [siteLimit, setSiteLimit] = useState(12)
  const [fitToken, setFitToken] = useState(0)

  /* ---------------- 问答助手取位置 ---------------- */
  const [qaQuestion, setQaQuestion] = useState('')
  const [qaBusy, setQaBusy] = useState(false)
  const [qaAnswer, setQaAnswer] = useState('')
  const [qaError, setQaError] = useState('')
  const [qaNote, setQaNote] = useState('')

  /* ---------------- 站点下钻抽屉（与 ?page=gis 同一组件/接口） ---------------- */
  const [siteDrawerOpen, setSiteDrawerOpen] = useState(false)
  const [selectedSite, setSelectedSite] = useState('')
  const [drilldown, setDrilldown] = useState<DrilldownData | null>(null)
  const [drillLoading, setDrillLoading] = useState(false)
  const [drillError, setDrillError] = useState('')
  const [analytics, setAnalytics] = useState<DrilldownAnalytics | null>(null)
  const [analyticsLoading, setAnalyticsLoading] = useState(false)
  const [drillRangeDays, setDrillRangeDays] = useState(7)
  const drillReqRef = useRef(0)

  /* ---------------- 链路 / 订阅者抽屉 ---------------- */
  const [mwLink, setMwLink] = useState<TopologyLink | null>(null)
  const [fiberLink, setFiberLink] = useState<FiberLink | null>(null)
  const [subOpen, setSubOpen] = useState(false)

  /* ---------------- 计算：最近一圈 / 最近链路 / 轨迹小区 ---------------- */
  /* ringOf 需要 { lat, lon }；站点对象用的是 lng，这里显式转换一次，
     避免把「经度叫 lng 还是 lon」的差异扩散到 geo.ts 的通用算法里。 */
  const sitePoints = useMemo(
    () => data.sites
      .filter((s) => isValidLatLon(Number(s.lat), Number(s.lng)))
      .map((s) => ({ site: s, lat: Number(s.lat), lon: Number(s.lng) })),
    [data.sites],
  )
  const ringRaw = useMemo(
    () => ringOf(sitePoints, location, { radiusKm, limit: siteLimit }),
    [sitePoints, location, radiusKm, siteLimit],
  )
  const ring = useMemo(
    () => ({ ...ringRaw, items: ringRaw.items.map((x) => ({ site: x.item.site, km: x.km })) }),
    [ringRaw],
  )

  const mwNear = useMemo(() => data.mwLinks
    .map((l) => ({
      link: l,
      km: distanceToSegmentKm(location, { lat: l.lat_a, lon: l.lng_a }, { lat: l.lat_b, lon: l.lng_b }),
    }))
    .sort((a, b) => a.km - b.km)
    .slice(0, LINK_DRAW_COUNT), [data.mwLinks, location])

  const fiberNear = useMemo(() => data.fiberLinks
    .map((f) => ({
      link: f,
      km: distanceToSegmentKm(location, { lat: f.aLat, lon: f.aLon }, { lat: f.bLat, lon: f.bLon }),
    }))
    .sort((a, b) => a.km - b.km)
    .slice(0, LINK_DRAW_COUNT), [data.fiberLinks, location])

  const nearestMw = mwNear[0]?.link ?? null
  const nearestFiber = fiberNear[0]?.link ?? null

  /* 诊断源（1100/1300/1301）里的 start_time → 小区最后出现时间。
     CCA 的 usertrack 本身没有时间戳，这一层只作补充，匹配不到就是空。 */
  const trackTimes = useMemo(() => buildTrackTimes(sub.sources), [sub.sources])

  /** 订阅者驻留小区 → 地图轨迹点（同一小区只留一个；缺坐标的丢弃并计数）。 */
  const track = useMemo(() => {
    const cells = sub.result?.cells?.cells || []
    const seen = new Set<string>()
    const out: TrackCell[] = []
    let missing = 0
    cells.forEach((c, i) => {
      const lat = Number(c.celllat)
      const lon = Number(c.celllon)
      if (!isValidLatLon(lat, lon)) { missing += 1; return }
      const key = cellKeyOf(c, i)
      if (seen.has(key)) return
      seen.add(key)
      const t = timeOfCell(trackTimes, c)
      out.push({
        key, name: c.cellname || c.cellkey || key, lat, lon,
        vendor: c.vendor, rat: c.rat,
        lastSeen: t?.lastSeen, hits: t?.hits,
      })
    })
    return { cells: out, missing }
  }, [sub.result, trackTimes])

  /** 查询时间窗（诊断窗口优先，回退 CCA 窗口）——展示在轨迹卡上，回答「时间信息」。 */
  const subWindowText = useMemo(() => {
    const w = sub.diag?.window
    if (w?.start && w?.end) return `${w.start} ~ ${w.end}`
    const cw = sub.result?.window
    if (cw?.starttime && cw?.endtime) return `${cw.starttime} ~ ${cw.endtime}`
    return ''
  }, [sub.diag, sub.result])

  const trackWithKm = useMemo(
    () => track.cells.map((c) => ({ cell: c, km: haversineKm(location, { lat: c.lat, lon: c.lon }) })),
    [track.cells, location],
  )

  /* ---------------- 动作 ---------------- */
  const applyLocation = useCallback((p: LatLon) => {
    if (!isValidLatLon(p.lat, p.lon)) {
      setLocError(t('loc.error.invalid'))
      return
    }
    setLocation(p)
    setLatText(p.lat.toFixed(6))
    setLonText(p.lon.toFixed(6))
    setLocError('')
    writeParams({ lat: p.lat.toFixed(6), lon: p.lon.toFixed(6) })
  }, [t])

  const applyManual = useCallback(() => {
    const lat = Number(latText)
    const lon = Number(lonText)
    if (!isValidLatLon(lat, lon)) {
      setLocError(t('loc.error.invalid'))
      return
    }
    applyLocation({ lat, lon })
  }, [applyLocation, latText, lonText, t])

  const useMyLocation = useCallback(() => {
    if (!navigator.geolocation) {
      setLocError(t('loc.geo.unsupported'))
      return
    }
    setGeoBusy(true)
    setLocError('')
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setGeoBusy(false)
        applyLocation({ lat: pos.coords.latitude, lon: pos.coords.longitude })
      },
      (err) => {
        setGeoBusy(false)
        setLocError(err.code === err.PERMISSION_DENIED ? t('loc.geo.denied') : t('loc.geo.failed'))
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 },
    )
  }, [applyLocation, t])

  const askQaForLocation = useCallback(async () => {
    const q = qaQuestion.trim()
    if (!q) {
      setQaError(t('loc.qa.needQuestion'))
      return
    }
    setQaBusy(true)
    setQaError('')
    setQaNote('')
    setQaAnswer('')
    try {
      const { data: resp } = await api.post<{ answer?: string }>(
        '/portal-qa/ask',
        { question: q, lang: locale, clientId: 'location-page' },
        { timeout: 300_000 },
      )
      const answer = String(resp?.answer || '')
      setQaAnswer(answer)
      const p = parseLatLon(answer)
      if (p) {
        applyLocation(p)
        return
      }
      /* 兜底：站点属性答案里本来就带「站号」（如 `站号：4067`）。
         问答助手版本还没更新到输出坐标时，用站号去站点主数据里换坐标 ——
         问的还是同一个站、答案仍来自 /api/portal-qa/ask，只是坐标由主数据补全。
         找不到就如实报「没有坐标」，不猜。 */
      const siteHit = siteFromAnswer(answer, data.sites)
      if (siteHit && isValidLatLon(Number(siteHit.lat), Number(siteHit.lng))) {
        applyLocation({ lat: Number(siteHit.lat), lon: Number(siteHit.lng) })
        setQaNote(t('loc.qa.resolvedBySite', { site: siteHit.site_name, id: siteHit.site_id }))
        return
      }
      setQaError(t('loc.qa.noCoords'))
    } catch (err: unknown) {
      /* 后端的 detail 是给用户看的中文（如「问答服务暂时不可用…」），优先用它；
         拿不到才退回 axios 的技术文本（Request failed with status code 503）。 */
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      const msg = typeof detail === 'string' && detail
        ? detail
        : err instanceof Error ? err.message : String(err)
      setQaError(t('loc.qa.failed', { msg }))
    } finally {
      setQaBusy(false)
    }
  }, [applyLocation, data.sites, locale, qaQuestion, t])

  /* URL 带 msisdn 进来时自动跑一次订阅者查询（刷新/分享链接后不用再点） */
  const subBootRef = useRef(false)
  useEffect(() => {
    if (subBootRef.current) return
    if (!sub.msisdn || sub.searched || sub.diagLoading) return
    subBootRef.current = true
    void sub.run(sub.msisdn)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sub.msisdn, sub.searched, sub.diagLoading])

  /* 顶栏「按页刷新」：本页登记在 AR_INPLACE_REFRESH_PAGES（地图重挂载会闪），
     收到外壳的 myapp-refresh 事件后静默重取三层数据；订阅者若查过也跟着刷新。 */
  useEffect(() => {
    const onRefresh = () => {
      data.reload()
      if (sub.searched) sub.refresh()
    }
    document.addEventListener('myapp-refresh', onRefresh)
    return () => document.removeEventListener('myapp-refresh', onRefresh)
  }, [data.reload, sub.searched, sub.refresh])

  const openSite = useCallback(async (site: Site) => {
    const req = drillReqRef.current + 1
    drillReqRef.current = req
    setSelectedSite(site.site_id)
    setSiteDrawerOpen(true)
    setDrilldown(null)
    setAnalytics(null)
    setDrillError('')
    setDrillLoading(true)
    setAnalyticsLoading(true)
    try {
      const { data: dd } = await api.get<DrilldownData>(`/gis/drilldown/${site.site_id}`)
      if (drillReqRef.current === req) setDrilldown(dd)
    } catch (err: unknown) {
      if (drillReqRef.current === req) {
        setDrillError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      if (drillReqRef.current === req) setDrillLoading(false)
    }
    try {
      const { data: an } = await api.get<DrilldownAnalytics>(
        `/gis/drilldown/${site.site_id}/analytics`, { params: { days: drillRangeDays } },
      )
      if (drillReqRef.current === req) setAnalytics(an)
    } catch {
      // 图表口径失败只让图表空着，不让整个抽屉失败（与 GIS 同口径）
    } finally {
      if (drillReqRef.current === req) setAnalyticsLoading(false)
    }
  }, [drillRangeDays])

  const submitSubscriber = useCallback(() => {
    const q = sub.msisdn.trim()
    if (!q) return
    void sub.run(q)
  }, [sub])

  const layerErrors = [data.sitesError, data.mwError, data.fiberError].filter(Boolean)

  /* 门户 GIS Map 的 Location 页签（?maponly=1）：只渲染地图卡，不渲染
     站点/链路列表、控制条、轨迹表、标题卡；地图卡内部会切成无卡片壳的 100vh 画布。 */
  const renderMapCard = (bare: boolean) => (
    <LocationMapCard
      bare={bare}
            center={location}
            radiusKm={ring.radiusKm}
            ringSites={ring.items}
            mwLinks={mwNear.map((x) => x.link)}
            fiberLinks={fiberNear.map((x) => x.link)}
            nearestMw={nearestMw}
            nearestFiber={nearestFiber}
            trackCells={track.cells}
            fitToken={fitToken}
            loadingSites={data.loadingSites && !data.sites.length}
            onSite={(x) => void openSite(x)}
            onMw={setMwLink}
            onFiber={setFiberLink}
            onTrack={() => setSubOpen(true)}
            sub={{
              msisdn: sub.msisdn,
              onMsisdnChange: sub.setMsisdn,
              onSearch: submitSubscriber,
              loading: sub.loading,
              diagLoading: sub.diagLoading,
              searched: sub.searched,
              resultMsisdn: sub.result?.msisdn,
              cellCount: track.cells.length,
              missing: track.missing,
              hasResult: !!sub.result,
              onOpen: () => setSubOpen(true),
              range: sub.range,
              onRangeChange: sub.setRange,
            }}
            coord={{
              latText,
              lonText,
              onLatText: setLatText,
              onLonText: setLonText,
              onApply: applyManual,
              error: locError,
            }}
          />
  )

  if (mapOnly) {
    return (
      <div style={{ width: '100%', height: '100vh', margin: 0, padding: 0, overflow: 'hidden' }}>
        {renderMapCard(true)}
      </div>
    )
  }

  return (
    <div style={pageWrap}>
      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} xl={15}>
          {renderMapCard(false)}
        </Col>

        <Col xs={24} xl={9}>
          <SitePanel items={ring.items} radiusKm={ring.radiusKm} onOpen={(s) => void openSite(s)} />
          <div style={{ height: 12 }} />
          <LinksPanel
            mw={mwNear}
            fiber={fiberNear}
            loadingMw={data.loadingMw}
            loadingFiber={data.loadingFiber}
            onOpenMw={setMwLink}
            onOpenFiber={setFiberLink}
          />
        </Col>
      </Row>

      {/* 控制条：经纬度/半径/问答助手都放在页面最下面（用户 2026-11-23 要求）。
          地图上另有「按经纬度定位」与「查询订阅者」两个浮窗，常用操作不用滚到底。 */}
      <LocationControls
        latText={latText}
        lonText={lonText}
        onLatText={setLatText}
        onLonText={setLonText}
        onApply={applyManual}
        geoBusy={geoBusy}
        onUseMyLocation={useMyLocation}
        radiusKm={radiusKm}
        onRadiusKm={setRadiusKm}
        siteLimit={siteLimit}
        onSiteLimit={setSiteLimit}
        onFit={() => setFitToken((n) => n + 1)}
        onRefresh={data.reload}
        qaQuestion={qaQuestion}
        onQaQuestion={setQaQuestion}
        qaBusy={qaBusy}
        onAskQa={() => void askQaForLocation()}
        qaError={qaError}
        qaNote={qaNote}
        qaAnswer={qaAnswer}
        locError={locError}
        widened={ring.widened ? {
          radiusKm,
          withinOriginal: ring.withinOriginal,
          effectiveKm: ring.radiusKm,
        } : null}
      />


      {/* 订阅者轨迹结果表：放在页面最下面（用户 2026-11-23 要求）。
          输入窗已经在地图上，这里只负责展示本次查询的结果；
          表格行仍可点开右侧 VMAX 诊断抽屉。 */}
      <div style={{ height: 12 }} />
      <SubscriberPanel
        msisdn={sub.msisdn}
        searched={sub.searched}
        loading={sub.loading}
        diagLoading={sub.diagLoading}
        trackKm={trackWithKm}
        missing={track.missing}
        resultMsisdn={sub.result?.msisdn}
        hasResult={!!sub.result}
        windowText={subWindowText}
        onOpen={() => setSubOpen(true)}
      />

      <LocationIntroCard layerErrors={layerErrors} />

      {/* ---------------- 抽屉 ---------------- */}
      <SiteDrilldownDrawer
        open={siteDrawerOpen}
        onClose={() => setSiteDrawerOpen(false)}
        loading={drillLoading}
        drilldown={drilldown}
        selectedSite={selectedSite}
        mode="all"
        analytics={analytics}
        analyticsLoading={analyticsLoading}
        rangeDays={drillRangeDays}
        onRangeDaysChange={setDrillRangeDays}
      />
      {!!drillError && (
        <Alert
          type="error" showIcon message={t('loc.error.drilldown')} description={drillError}
          style={{ position: 'fixed', right: 24, bottom: 24, zIndex: 1200, maxWidth: 420 }}
        />
      )}

      <MwLinkDrawer link={mwLink} center={location} onClose={() => setMwLink(null)} />
      <FiberLinkDrawer link={fiberLink} center={location} onClose={() => setFiberLink(null)} />
      <SubscriberDrawer
        open={subOpen}
        msisdn={sub.result?.msisdn || sub.msisdn}
        diag={sub}
        onClose={() => setSubOpen(false)}
      />
    </div>
  )
}
