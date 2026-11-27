/* ============================================================================
 * OLIB FO 地理网络拓扑（?page=olibTopo_v2，2026-11-16）
 * ============================================================================
 * 页面定位：把 v1「可缩放的 PDF 拓扑图」升级为**真正的 GIS 网络视图**。
 *   · 默认视图 = 真实 Liberia 地理底图（自托管 PMTiles）+ 站点真实 GPS；
 *   · 站点按类型/区域/关键词筛选，密集区自动聚合；
 *   · 点击站点看详情、GPS 匹配来源与相连骨干链路；
 *   · 保留 PDF 原图作为参考视图（物理 / 逻辑 / 骨干示意），v1 页面完全不动。
 *
 * 数据口径：
 *   · 667 个站名与相对坐标来自 v1 的 networkTopology/topologyData.ts（PDF 提取）；
 *   · GPS 来自 public/olib/olib-site-geo.json（master_site_db 快照，见生成脚本）；
 *   · 骨干链路来自 v1 的 CORE_LINKS（PDF 第 2 页人工梳理），只画两端都有 GPS 的；
 *   · PDF 链路图层来自 public/olib/olib-fiber-links.json（第 1 页矢量线段数字化，
 *     见 backend/scripts/gen_olib_fiber_links.py），按站点对画细线，可开关。
 *
 * ⚠ 不修改 v1 的任何文件：本页只 import v1 的数据与 TopologyViewer，
 *   不改 topologyData.ts / TopologyViewer.tsx / NetworkTopology.tsx。
 * ========================================================================== */
import { useCallback, useMemo, useRef, useState } from 'react'
import { Alert, Button, Segmented, Spin, Switch } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

import { useI18n } from '../i18n'
import { useTheme } from '../theme'
import { CardBase } from '../components/cards'
import CalcChainNote from '../components/CalcChainNote'
import {
  CORE_LINKS, SITES,
  type CoreLinkType, type SiteFilter, type TopoSite, type TopoView,
} from './networkTopology/topologyData'
import TopologyViewer, { type TopologyViewerHandle } from './networkTopology/TopologyViewer'
import GeoTopologyMap from './networkTopologyV2/GeoTopologyMap'
import V2SiteSearch from './networkTopologyV2/V2SiteSearch'
import V2SiteDetail from './networkTopologyV2/V2SiteDetail'
import V2Legend from './networkTopologyV2/V2Legend'
import V2MatchModal from './networkTopologyV2/V2MatchModal'
import { buildGeoLinks, buildFiberLinks, buildGeoSites, filterGeoSites, linksOfSite } from './networkTopologyV2/geoData'
import { siteTypeLabel } from './networkTopologyV2/labels'
import { useOlibSiteGeo } from './networkTopologyV2/useSiteGeo'
import { useOlibFiberLinks } from './networkTopologyV2/useFiberLinks'
import type { GeoViewMode, GeoSite } from './networkTopologyV2/types'

/** 页面的四种视图：01 地理（默认）/ 02 物理 / 03 逻辑 / 04 骨干核心。 */
const MODES: { value: GeoViewMode; key: string }[] = [
  { value: 'geo', key: 'olibV2.mode.geo' },
  { value: 'physical', key: 'olibV2.mode.physical' },
  { value: 'logical', key: 'olibV2.mode.logical' },
  { value: 'core', key: 'olibV2.mode.core' },
]

function readInitialMode(): GeoViewMode {
  try {
    const v = new URLSearchParams(window.location.search).get('view')
    if (v === 'physical' || v === 'logical' || v === 'core' || v === 'geo') return v
  } catch { /* ignore */ }
  return 'geo'
}

const PAGE_CSS = `
.olibv2-page{display:flex;flex-direction:column;gap:var(--wh-space-md);
  flex:1 1 auto;height:100%;min-height:0}
.olibv2-split{display:flex;gap:12px;align-items:stretch;flex-wrap:wrap;flex:1 1 auto;min-height:0}
.olibv2-pane{min-width:0;min-height:0;height:100%;display:flex;flex-direction:column}
.olibv2-pane-map{flex:3 1 660px}
.olibv2-pane-side{flex:1 1 320px}
.olibv2-side{display:flex;flex-direction:column;gap:var(--wh-space-md);flex:1 1 auto;
  min-height:0;overflow-y:auto;padding-right:var(--wh-space-xs)}
.olibv2-side>*{flex:0 0 auto}
.olibv2-card{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
.olibv2-card .ant-card-body{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
.olibv2-toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;
  padding:var(--wh-space-sm) var(--wh-space-md);border-bottom:var(--wh-card-border)}
.olibv2-toolbar-info{font-size:11.5px;color:var(--wh-text-muted);line-height:1.35}
.olibv2-toolbar-spacer{flex:1 1 auto}
.olibv2-fiber-toggle{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;
  color:var(--wh-text-muted);cursor:pointer;white-space:nowrap}
.olibv2-map-wrap{position:relative;flex:1 1 auto;min-height:0;overflow:hidden}
.olibv2-map{position:absolute;inset:0}
.olibv2-map.is-dark .olibv2-name{background:rgba(6,14,28,.78);color:#dfe9fa}
.olibv2-overlay{position:absolute;inset:0;z-index:500;display:flex;align-items:flex-start;
  justify-content:center;padding-top:18vh;background:rgba(8,16,32,.10);pointer-events:none}
.olibv2-overlay .ant-alert,.olibv2-overlay .ant-spin{pointer-events:auto;max-width:min(420px,90%)}
.olibv2-error-raw{font-size:11px;color:var(--wh-text-dim);margin-top:4px;word-break:break-all}
.olibv2-ref{display:flex;flex-direction:column;flex:1 1 auto;height:100%;min-height:0;overflow:hidden}
.olibv2-ref .topo-fill{flex:1 1 auto;height:100%;min-height:0}
.olibv2-ref .topo-fill>.topo-viewer{flex:1 1 auto;height:auto;min-height:20rem}
/* 页面级四视图切换已覆盖 PDF 三视图，隐藏 TopologyViewer 内部的重复 tabs */
.olibv2-ref .topo-views{display:none!important}

/* ---------- Leaflet 覆盖层 ---------- */
/* iconSize 为 0×0 时必须 overflow:visible，否则站名会被裁掉 */
.olibv2-site-icon,.olibv2-cluster-icon,.olibv2-county-icon{background:transparent!important;border:0!important;
  overflow:visible!important}
.leaflet-marker-icon.olibv2-site-icon{overflow:visible!important}
.olibv2-site{position:absolute;left:0;top:0;width:0;height:0;overflow:visible;pointer-events:none}
.olibv2-dot{position:absolute;left:0;top:0;display:block;transform:translate(-50%,-50%);
  border-radius:50%;box-shadow:0 0 0 2px rgba(255,255,255,.9);pointer-events:auto;cursor:pointer}
.olibv2-site--normal .olibv2-dot{width:8px;height:8px;background:#5b7185}
.olibv2-site--5g .olibv2-dot{width:10px;height:10px;background:#ef4444}
.olibv2-site--isp .olibv2-dot{width:10px;height:10px;border-radius:2px;background:#0ea5e9;
  transform:translate(-50%,-50%) rotate(45deg)}
.olibv2-site--core .olibv2-dot{width:14px;height:14px;background:#7c3aed;border:3px solid #fff;
  box-shadow:0 0 0 3px rgba(124,58,237,.35)}
.olibv2-site.is-selected .olibv2-dot{box-shadow:0 0 0 4px rgba(255,121,0,.45)}
.olibv2-site.is-selected .olibv2-dot::after{content:'';position:absolute;inset:-7px;
  border-radius:inherit;border:2px solid #ff7900;animation:olibv2-pulse 1.6s ease-out infinite}
@keyframes olibv2-pulse{0%{opacity:.95;transform:scale(.85)}70%{opacity:0;transform:scale(1.5)}100%{opacity:0;transform:scale(1.5)}}
.olibv2-name{position:absolute;left:10px;top:-9px;display:none;max-width:190px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;font-size:11px;line-height:1.25;padding:1px 5px;
  border-radius:4px;background:rgba(225,236,250,.92);color:#1c2c44;pointer-events:none;
  box-shadow:0 1px 4px rgba(0,0,0,.18);z-index:2}
/* 骨干核心站名始终显示（数量少，国家尺度也要认得出来） */
.olibv2-site--core .olibv2-name,
.olibv2-site.is-selected .olibv2-name{display:block}
/* zoom≥8（默认全国视图）：ISP/5G；zoom≥10：全部站名 */
.olibv2-labels-major .olibv2-site--isp .olibv2-name,
.olibv2-labels-major .olibv2-site--5g .olibv2-name{display:block}
.olibv2-labels-all .olibv2-site .olibv2-name{display:block}
.olibv2-cluster{display:flex;align-items:center;justify-content:center;border-radius:50%;
  background:rgba(37,140,255,.16);border:2px solid rgba(37,140,255,.8);color:var(--wh-text);
  font-size:12px;font-weight:600;box-shadow:0 0 0 4px rgba(37,140,255,.10);cursor:pointer}
.olibv2-county{position:absolute;left:0;top:0;transform:translate(-50%,-50%);font-size:10px;
  font-weight:600;letter-spacing:.08em;white-space:nowrap;pointer-events:none;opacity:.85}
.leaflet-tooltip.olibv2-tip{background:#0d1c33;color:#e6f0ff;border:1px solid rgba(90,130,190,.55);
  border-radius:6px;font-size:12px;line-height:1.4;padding:4px 8px;box-shadow:0 4px 14px rgba(0,0,0,.3)}
.leaflet-tooltip-top.olibv2-tip::before{border-top-color:#0d1c33}
.leaflet-tooltip.olibv2-tip strong{color:#fff}
.olibv2-link-pop{font-size:12px;line-height:1.5}

/* ---------- 右侧面板 ---------- */
.olibv2-filter-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
.olibv2-matchsum{display:flex;gap:8px;flex-wrap:wrap;font-size:12px;color:var(--wh-text-muted);margin-top:8px}
.olibv2-matchsum .is-warn{color:var(--wh-warn)}
.olibv2-res-count{font-size:11px;color:var(--wh-text-dim);margin:6px 0 4px}
.olibv2-res-list{display:flex;flex-direction:column;gap:2px;max-height:232px;overflow-y:auto;margin:0 -4px}
.olibv2-res{display:flex;flex-direction:column;gap:1px;text-align:left;background:transparent;
  border:1px solid transparent;border-radius:6px;padding:5px 7px;cursor:pointer;color:var(--wh-text);font:inherit}
.olibv2-res:hover{background:rgba(37,140,255,.08)}
.olibv2-res.is-selected{background:rgba(255,121,0,.12);border-color:rgba(255,121,0,.5)}
.olibv2-res.is-unlocated{opacity:.68}
.olibv2-res-main{display:flex;align-items:center;gap:5px;min-width:0}
.olibv2-res-name{font-size:12.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.olibv2-res-meta{font-size:11px;color:var(--wh-text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.olibv2-tag{font-size:10px;line-height:1.4;padding:0 4px;border-radius:4px;white-space:nowrap;
  border:1px solid currentColor;opacity:.9}
.olibv2-tag.is-core{color:#a78bfa}
.olibv2-tag.is-isp{color:#38bdf8}
.olibv2-tag.is-5g{color:#f87171}
.olibv2-empty{font-size:12px;color:var(--wh-text-muted);padding:10px 0;line-height:1.6}
.olibv2-empty.is-tight{padding:4px 0}
.olibv2-detail-head{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.olibv2-detail-name{font-size:15px;font-weight:700;min-width:0;overflow-wrap:anywhere}
.olibv2-kind-dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex:0 0 auto}
.olibv2-kind-dot.is-core{background:#7c3aed}
.olibv2-kind-dot.is-isp{background:#0ea5e9;border-radius:2px;transform:rotate(45deg)}
.olibv2-kind-dot.is-5g{background:#ef4444}
.olibv2-kind-dot.is-normal{background:#5b7185}
.olibv2-kv{display:flex;gap:10px;justify-content:space-between;align-items:baseline;
  font-size:12px;padding:3px 0;border-bottom:1px dashed rgba(128,150,180,.18)}
.olibv2-kv>span{flex:0 0 auto;color:var(--wh-text-muted)}
.olibv2-kv>b{font-weight:500;text-align:right;min-width:0;overflow-wrap:anywhere}
.olibv2-mono{font-variant-numeric:tabular-nums}
.olibv2-detail-note{font-size:11px;color:var(--wh-text-dim);margin-top:6px;line-height:1.5}
.olibv2-cands{margin-top:4px;font-size:11px;color:var(--wh-text-muted)}
.olibv2-verified{display:inline-block;margin-left:6px;font-size:10px;padding:0 4px;border-radius:4px;
  border:1px solid currentColor;color:var(--wh-warn)}
.olibv2-verified.is-ok{color:var(--wh-ok)}
.olibv2-detail-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.olibv2-sec-title{font-size:11.5px;font-weight:600;color:var(--wh-text-muted);margin:12px 0 6px;
  letter-spacing:.02em}
.olibv2-link-list{display:flex;flex-direction:column;gap:2px}
.olibv2-link-row{display:flex;align-items:center;gap:7px;background:transparent;border:1px solid transparent;
  border-radius:5px;padding:4px 5px;cursor:pointer;color:var(--wh-text);font:inherit;text-align:left;width:100%}
.olibv2-link-row:hover{background:rgba(37,140,255,.08)}
.olibv2-link-line{width:18px;border-top-width:2px;border-top-style:solid;flex:0 0 auto}
.olibv2-link-name{flex:1 1 auto;min-width:0;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.olibv2-link-type{font-size:10px;color:var(--wh-text-muted);white-space:nowrap}
.olibv2-legend-sec{font-size:11px;font-weight:600;color:var(--wh-text-muted);margin:2px 0 6px}
.olibv2-legend-sec:not(:first-child){margin-top:10px}
.olibv2-legend-grid{display:grid;grid-template-columns:1fr 1fr;gap:4px 10px}
.olibv2-legend-links{display:flex;flex-direction:column;gap:4px}
.olibv2-legend-item{display:flex;align-items:center;gap:6px;font-size:12px;min-width:0}
.olibv2-legend-line{width:22px;border-top-width:2px;border-top-style:solid;flex:0 0 auto}
.olibv2-legend-stats{font-size:11px;color:var(--wh-text-muted);margin-top:10px;line-height:1.5}
.olibv2-legend-note{font-size:11px;color:var(--wh-text-dim);margin-top:4px;line-height:1.55}
.olibv2-legend-warn{font-size:11px;color:var(--wh-warn);margin-top:4px;line-height:1.55}
.olibv2-foot{display:flex;justify-content:flex-end;flex:0 0 auto;padding-right:var(--wh-space-sm)}
.olibv2-match-hint{font-size:12px;color:var(--wh-text-muted);line-height:1.6;margin-bottom:8px}
.olibv2-match-count{font-size:12px;color:var(--wh-text-muted)}
.olibv2-unmatched-list{display:flex;flex-direction:column;gap:0;max-height:62vh;overflow-y:auto;
  border-top:1px dashed rgba(128,150,180,.25)}
.olibv2-unmatched-row{display:grid;grid-template-columns:minmax(120px,1.1fr) minmax(90px,.9fr) minmax(160px,1.6fr);
  gap:10px;padding:6px 4px;font-size:12px;border-bottom:1px dashed rgba(128,150,180,.18)}
.olibv2-unmatched-name{font-weight:600;overflow-wrap:anywhere}
.olibv2-unmatched-sector{color:var(--wh-text-muted)}
.olibv2-unmatched-reason{color:var(--wh-text-muted);overflow-wrap:anywhere}
@media (max-width:999.98px){
  .olibv2-page,.olibv2-split{height:auto;flex:0 0 auto}
  .olibv2-pane{height:auto}
  .olibv2-pane-map{flex:1 1 100%}
  .olibv2-pane-side{flex:1 1 100%}
  .olibv2-map-wrap{min-height:62vh}
  .olibv2-side{overflow:visible}
  .olibv2-unmatched-row{grid-template-columns:1fr 1fr}
  .olibv2-unmatched-reason{grid-column:1 / -1}
}
`

export default function NetworkTopologyV2() {
  const { t } = useI18n()
  const { isDark } = useTheme()
  const { file, loading, error, reload } = useOlibSiteGeo()
  const { file: fiberFile, error: fiberError } = useOlibFiberLinks()

  const sites = useMemo(() => buildGeoSites(file), [file])
  const { links, skipped: skippedLinks } = useMemo(() => buildGeoLinks(sites), [sites])
  const { links: fiberLinks } = useMemo(() => buildFiberLinks(sites, fiberFile), [sites, fiberFile])
  const locatedCount = useMemo(() => sites.filter((s) => s.located).length, [sites])
  const unmatched = useMemo(() => sites.filter((s) => !s.located), [sites])
  const fiberSites = useMemo(() => {
    const ids = new Set<number>()
    for (const l of fiberLinks) { ids.add(l.a.index); ids.add(l.b.index) }
    return ids.size
  }, [fiberLinks])

  const [mode, setMode] = useState<GeoViewMode>(readInitialMode)
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [focus, setFocus] = useState<{ index: number; token: number; zoom?: number } | null>(null)
  const [query, setQuery] = useState('')
  const [sector, setSector] = useState('')
  const [filter, setFilter] = useState<SiteFilter>('all')
  const [resetToken, setResetToken] = useState(0)
  const [sitesVisible, setSitesVisible] = useState(true)
  const [fiberOn, setFiberOn] = useState(true)
  const [matchOpen, setMatchOpen] = useState(false)
  const [matchAnchor, setMatchAnchor] = useState<{ x: number; y: number } | null>(null)
  const tokenRef = useRef(0)
  const viewerRef = useRef<TopologyViewerHandle>(null)

  const selected = selectedIndex != null ? (sites[selectedIndex] ?? null) : null
  const results = useMemo(
    () => filterGeoSites(sites, query, sector, filter),
    [sites, query, sector, filter],
  )
  const mapSites = useMemo(() => results.filter((s) => s.located), [results])
  const selectedLinks = useMemo(() => linksOfSite(selected, links), [selected, links])

  const nextToken = () => { tokenRef.current += 1; return tokenRef.current }

  /* 定位一律留在当前页签：地理图飞到 GPS；物理/逻辑/骨干走 TopologyViewer.focusHere */
  const locate = useCallback((site: GeoSite) => {
    setSelectedIndex(site.index)
    if (mode === 'geo') {
      if (site.located) setFocus({ index: site.index, token: nextToken() })
      return
    }
    viewerRef.current?.locate(site)
  }, [mode])

  const zoomTo = useCallback((site: GeoSite) => {
    setSelectedIndex(site.index)
    if (mode === 'geo') {
      if (site.located) setFocus({ index: site.index, token: nextToken(), zoom: 13 })
      return
    }
    viewerRef.current?.zoomTo(site)
  }, [mode])

  const pick = useCallback((site: GeoSite) => {
    setSelectedIndex(site.index)
    if (mode === 'geo') {
      if (site.located) setFocus({ index: site.index, token: nextToken() })
      return
    }
    viewerRef.current?.focusHere(site)
  }, [mode])

  const onSelectIndex = useCallback((index: number) => setSelectedIndex(index), [])

  const onViewerSelect = useCallback((site: TopoSite | null) => {
    if (!site) { setSelectedIndex(null); return }
    let i = SITES.indexOf(site)
    if (i < 0) i = SITES.findIndex((s) => s.name === site.name && s.x === site.x && s.y === site.y)
    if (i >= 0) setSelectedIndex(i)
  }, [])

  const typeLabel = useCallback((site: GeoSite) => siteTypeLabel(site, t), [t])
  const clusterLabel = useCallback((n: number) => t('olibV2.map.cluster', { n }), [t])
  const linkTypeLabel = useCallback((type: CoreLinkType) => {
    switch (type) {
      case 'dwdm': return t('olibV2.legend.link.dwdm')
      case 'rail': return t('olibV2.legend.link.rail')
      case 'p25a': return t('olibV2.legend.link.p25a')
      default: return t('olibV2.legend.link.exist')
    }
  }, [t])

  const reset = () => {
    if (mode === 'geo') setResetToken((n) => n + 1)
    else viewerRef.current?.fit()
  }

  const openUnmatched = (e: React.MouseEvent<HTMLElement>) => {
    setMatchAnchor({ x: e.clientX, y: e.clientY })
    setMatchOpen(true)
  }

  const refView: TopoView = mode === 'core' ? 'core' : mode === 'logical' ? 'logical' : 'physical'

  return (
    <div className="olibv2-page">
      <style>{PAGE_CSS}</style>
      <div className="olibv2-split">
        <div className="olibv2-pane olibv2-pane-map">
          <CardBase
            className="olibv2-card"
            bodyStyle={{ padding: 0, overflow: 'hidden', flex: '1 1 auto', minHeight: 0 }}
          >
            <div className="olibv2-toolbar">
              <Segmented
                className="wh-seg"
                size="small"
                value={mode}
                onChange={(v) => setMode(v as GeoViewMode)}
                options={MODES.map((m) => ({ value: m.value, label: t(m.key) }))}
              />
              <div className="olibv2-toolbar-spacer" />
              {mode === 'geo' && (
                <>
                  <span className="olibv2-toolbar-info">
                    {t('olibV2.map.stats', {
                      sites: mapSites.length,
                      links: links.length,
                      fiber: fiberOn ? fiberLinks.length : 0,
                    })}
                  </span>
                  <label className="olibv2-fiber-toggle">
                    <Switch size="small" checked={fiberOn} onChange={setFiberOn} />
                    <span>{t('olibV2.fiber.toggle')}</span>
                  </label>
                </>
              )}
              <Button size="small" icon={<ReloadOutlined />} onClick={reset}>
                {t('olibV2.reset')}
              </Button>
            </div>

            {mode === 'geo' ? (
              <div className="olibv2-map-wrap">
                <GeoTopologyMap
                  sites={mapSites}
                  links={links}
                  fiberLinks={fiberOn ? fiberLinks : []}
                  selected={selected}
                  focus={focus}
                  resetToken={resetToken}
                  isDark={isDark}
                  onSelect={onSelectIndex}
                  typeLabel={typeLabel}
                  clusterLabel={clusterLabel}
                  linkTypeLabel={linkTypeLabel}
                />
                {(loading || error) && (
                  <div className="olibv2-overlay">
                    {error ? (
                      <Alert
                        type="error"
                        showIcon
                        message={t('olibV2.error.title')}
                        description={(
                          <div>
                            <div>{t('olibV2.error.desc')}</div>
                            <div className="olibv2-error-raw">{error}</div>
                          </div>
                        )}
                        action={<Button size="small" onClick={reload}>{t('olibV2.retry')}</Button>}
                      />
                    ) : (
                      <Spin tip={t('olibV2.loading')} size="large" />
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="olibv2-ref">
                <div className="topo-fill">
                  <TopologyViewer
                    ref={viewerRef}
                    view={refView}
                    onViewChange={(v) => setMode(v)}
                    sitesVisible={sitesVisible}
                    onSitesVisibleChange={setSitesVisible}
                    selected={selected}
                    onSelect={onViewerSelect}
                  />
                </div>
              </div>
            )}
          </CardBase>
        </div>

        <div className="olibv2-pane olibv2-pane-side">
          <div className="olibv2-side">
            <V2SiteSearch
              query={query}
              onQuery={setQuery}
              sector={sector}
              onSector={setSector}
              filter={filter}
              onFilter={setFilter}
              results={results}
              total={sites.length}
              located={locatedCount}
              selected={selected}
              onPick={pick}
              onShowUnmatched={openUnmatched}
            />
            <V2SiteDetail
              site={selected}
              links={selectedLinks}
              locateEnabled={mode !== 'geo' || !!selected?.located}
              onLocate={locate}
              onZoom={zoomTo}
              onSelect={onSelectIndex}
              onClear={() => setSelectedIndex(null)}
            />
            <V2Legend
              drawnSites={locatedCount}
              totalSites={sites.length}
              drawnLinks={links.length}
              skippedLinks={skippedLinks}
              fiberLinks={fiberOn ? fiberLinks.length : 0}
              fiberSites={fiberSites}
              fiberError={fiberError}
            />
          </div>
        </div>
      </div>

      <div className="olibv2-foot">
        <CalcChainNote
          trigger="icon"
          title={t('olibV2.note.title')}
          sections={[
            {
              title: t('olibV2.note.sec.source'),
              lines: [t('olibV2.note.source.1'), t('olibV2.note.source.2', { date: file?.generatedAt ?? '—' })],
            },
            {
              title: t('olibV2.note.sec.match'),
              lines: [
                t('olibV2.note.match.1', { located: locatedCount, total: sites.length }),
                t('olibV2.note.match.2'),
              ],
            },
            {
              title: t('olibV2.note.sec.links'),
              lines: [
                t('olibV2.note.links.1', { drawn: links.length, total: CORE_LINKS.length, skipped: skippedLinks }),
                t('olibV2.note.links.2'),
              ],
            },
            {
              title: t('olibV2.note.sec.fiber'),
              lines: [
                t('olibV2.note.fiber.1', { n: fiberLinks.length, sites: fiberSites }),
                t('olibV2.note.fiber.2'),
              ],
            },
            {
              title: t('olibV2.note.sec.limits'),
              lines: [t('olibV2.note.limits.1')],
            },
          ]}
        />
      </div>

      <V2MatchModal
        open={matchOpen}
        onClose={() => setMatchOpen(false)}
        sites={unmatched}
        anchor={matchAnchor}
      />
    </div>
  )
}
