/* ============================================================================
 * OLIB FO 网络拓扑（?page=olibTopo）
 * ============================================================================
 * 页面定位：把 Orange Liberia《Orange network topology_20260623.pdf》第 1 页物理图、
 * 《OLIB FO_Network_20260901.pdf》逻辑图（2026-11-21 换版）和 667 个提取站点做成一张
 * 可缩放、可搜索、可定位的参考图。**纯静态参考页**，
 * 不取接口、不写库，所以 AR_PAGE_DEFAULTS 取最松的 1800 秒（见 App.tsx）。
 *
 * 组成：
 *   · 左：画布（物理图 + 站点点位 / 逻辑图 / 骨干示意）；
 *   · 右：站点模糊查询、选中站点详情、图例；
 *   · 页面最下面只留一个问号（`CalcChainNote trigger="icon"`），点开看数据口径与来源。
 *   · ?maponly=1（门户 IP 页签）：只留物理图，搜索框浮在画布左上角。
 *
 * 2026-11-16 用户要求：去掉顶部「提取站点 / 骨干核心 / 骨干光纤 / AML 数据中心」四张
 * 统计卡（数字都在图例与说明里，卡只占首屏），口径说明也从一行收起条改成页面底部
 * 一个问号 —— 首屏全部让给画布，说明按需点开；同日去掉「站点分布」视图。
 *
 * 2026-11-20 用户要求：本页**去掉 backbone（骨干示意）页签**，只留「物理 / 逻辑」两图。
 *   · 骨干示意本身没删死代码：v2（?page=olibTopo_v2）仍用同一个 TopologyViewer 的
 *     core 模式（它有自己的四视图 Segmented），所以由本页通过 views 只声明两档；
 *   · ?view=core 的旧链接按物理图兜底（readInitialView 不再认 core）。
 *
 * 门户内嵌（2026-11-20 起）：Knowledge Q&A 的「MW Topology」页签要物理图、
 *   「Fiber Topology」页签要逻辑图，所以 maponly 模式不再写死物理图，
 *   改为认 ?view=（见 readMapOnlyMode + mapOnly 分支）。
 *
 * 数据与坐标口径见 networkTopology/topologyData.ts 文件头。
 * ========================================================================== */
import { useCallback, useMemo, useRef, useState } from 'react'

import { useI18n } from '../i18n'
import { CardBase } from '../components/cards'
import CalcChainNote from '../components/CalcChainNote'
import {
  SITES,
  type SiteFilter, type TopoSite, type TopoView,
} from './networkTopology/topologyData'
import TopologyViewer, { type TopologyViewerHandle } from './networkTopology/TopologyViewer'
import SiteSearch from './networkTopology/SiteSearch'
import MapSiteSearch from './networkTopology/MapSiteSearch'
import SiteDetail from './networkTopology/SiteDetail'
import TopologyLegend from './networkTopology/TopologyLegend'
import { matchSites } from './networkTopology/siteMatch'

/** 门户 GIS Map 的 IP 页签（`?maponly=1`）只留拓扑图，搜索框浮在画布上。
 *  2026-11-20 起不再固定物理图：门户 Knowledge Q&A 的 Fiber Topology 页签要逻辑图，
 *  故这里认 `?view=`（只认 physical / logical）。 */
function readMapOnlyMode(): boolean {
  try {
    const v = new URLSearchParams(window.location.search).get('maponly')
    return v === '1' || v === 'true'
  } catch { return false }
}

/** 本页可选的视图：2026-11-20 起只剩「物理 / 逻辑」（骨干示意页签已去掉） */
const PAGE_VIEWS: TopoView[] = ['physical', 'logical']

/** 认 ?view=，非法值（含已下线的 core）一律回落到物理图 */
function readInitialView(): TopoView {
  const v = new URLSearchParams(window.location.search).get('view')
  return v === 'logical' ? 'logical' : 'physical'
}

const MAPONLY_CSS = `
.olib-maponly{position:relative;height:100vh;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.olib-maponly .topo-views,.olib-maponly .topo-hint{display:none!important}
.olib-maponly .topo-toolbar{flex:0 0 auto}
.olib-maponly .topo-viewer{flex:1 1 auto;height:auto!important;min-height:0}
.olib-finder{position:absolute;z-index:20;left:10px;top:52px;width:min(340px,calc(100% - 20px))}
`

/* 正常模式（非 maponly）的页面骨架。
 *  - 页面根部认领内容区剩余高度：embed 下 #main-content 是定高 100vh，画布卡片跟着撑满，
 *    地图不再固定 62vh，卡片下方不会留一大块空白（2026-11-16 用户要求）；
 *  - 左右两栏用普通 flex 而不是 antd Row/Col：Row 的 flex 行高在「内容比可用高度高」时
 *    不参与收缩，Col 会被内容撑到 1010px 再溢出（实测 embed 900px 视口下画布卡片伸出视野）；
 *  - 右栏高度受页面约束时在自己容器里滚动。子卡片必须 flex-shrink:0 —— 否则会被压扁、
 *    内容被 overflow:hidden 裁掉，而容器 scrollHeight 又等于自身高度，滚也滚不到
 *    （图例底部就是这样消失的）；
 *  - 窄屏（<1000px，两栏会换行）回到按内容高度排布（并保留原 52vh 地图高度），
 *    与改造前的窄屏行为一致。 */
const PAGE_CSS = `
.olib-fill-page{display:flex;flex-direction:column;gap:var(--wh-space-md);
  flex:1 1 auto;height:100%;min-height:0}
.olib-split{display:flex;gap:12px;align-items:stretch;flex-wrap:wrap;flex:1 1 auto;min-height:0}
.olib-pane{min-width:0;min-height:0;height:100%;display:flex;flex-direction:column}
.olib-pane-map{flex:3 1 640px}
.olib-pane-side{flex:1 1 300px}
.olib-side{display:flex;flex-direction:column;gap:var(--wh-space-md);flex:1 1 auto;
  min-height:0;overflow-y:auto;padding-right:var(--wh-space-xs)}
.olib-side>*{flex:0 0 auto}
.olib-scope-bar{display:flex;justify-content:flex-end;align-items:center;line-height:1;
  flex:0 0 auto;padding-right:var(--wh-space-sm);padding-bottom:var(--wh-space-sm)}
@media (max-width:999.98px){
  /* 太窄时两栏会换行堆叠。此时若继续「占满高度」，堆叠出来的两段各拿 100% 高度，
     在 embed（#main-content 定高且不可滚）里会互相压住、问号浮到搜索卡上面；
     所以窄屏回到按内容高度排布 —— 与改造前一致，直链下正常滚动，embed 下按老规矩裁断。 */
  .olib-fill-page,.olib-split{height:auto;flex:0 0 auto}
  .olib-pane{height:auto}
  .olib-fill-page .topo-fill>.topo-viewer{min-height:52vh}
}
`

export default function NetworkTopology() {
  const { t } = useI18n()
  const viewer = useRef<TopologyViewerHandle>(null)
  const [mapOnly] = useState(readMapOnlyMode)

  const [view, setView] = useState<TopoView>(readInitialView)
  const [selected, setSelected] = useState<TopoSite | null>(null)
  const [sitesVisible, setSitesVisible] = useState(true)
  const [query, setQuery] = useState('')
  const [sector, setSector] = useState('')
  const [filter, setFilter] = useState<SiteFilter>('all')

  const results = useMemo(
    () => matchSites(SITES, query, sector, filter),
    [query, sector, filter],
  )

  /* 搜索 / 详情定位一律留在当前页签，不切物理图 */
  const pick = useCallback((s: TopoSite) => { viewer.current?.focusHere(s) }, [])
  const locate = useCallback((s: TopoSite) => { viewer.current?.locate(s) }, [])
  const zoomTo = useCallback((s: TopoSite) => { viewer.current?.zoomTo(s) }, [])

  if (mapOnly) {
    return (
      <div className="olib-maponly">
        <style>{MAPONLY_CSS}</style>
        <TopologyViewer
          ref={viewer}
          view={view}
          onViewChange={() => {}}
          views={PAGE_VIEWS}
          sitesVisible={sitesVisible}
          onSitesVisibleChange={setSitesVisible}
          selected={selected}
          onSelect={setSelected}
        />
        <MapSiteSearch
          query={query}
          onQuery={setQuery}
          results={results}
          total={SITES.length}
          onPick={pick}
        />
      </div>
    )
  }

  return (
    /* 高度自适应（2026-11-16 用户要求）：页面根部认领内容区剩余高度（embed 下
       #main-content 是定高 100vh，直链下按内容高度），画布卡片跟着撑满，
       地图不再固定 62vh —— 否则卡片下方会留一大块空白。
       右栏内容比画布长时在自己的容器里滚动，不把整页顶高。骨架见 PAGE_CSS。 */
    <div className="olib-fill-page">
      <style>{PAGE_CSS}</style>
      <div className="olib-split">
        <div className="olib-pane olib-pane-map">
          <CardBase
            style={{ flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column' }}
            bodyStyle={{
              padding: 0, overflow: 'hidden', flex: '1 1 auto', minHeight: 0,
              display: 'flex', flexDirection: 'column',
            }}
          >
            <div className="topo-fill">
              <TopologyViewer
                ref={viewer}
                view={view}
                onViewChange={setView}
                views={PAGE_VIEWS}
                sitesVisible={sitesVisible}
                onSitesVisibleChange={setSitesVisible}
                selected={selected}
                onSelect={setSelected}
              />
            </div>
          </CardBase>
        </div>
        <div className="olib-pane olib-pane-side">
          <div className="olib-side">
            <SiteSearch
              query={query}
              onQuery={setQuery}
              sector={sector}
              onSector={setSector}
              filter={filter}
              onFilter={setFilter}
              results={results}
              total={SITES.length}
              selected={selected}
              onPick={pick}
            />
            <SiteDetail site={selected} onLocate={locate} onZoom={zoomTo} />
            <TopologyLegend />
          </div>
        </div>
      </div>

      {/* 数据口径与来源：页面最下面只留一个问号，点开看说明（不占一行、不占首屏）。
          口径说明仍走统一载体 CalcChainNote，只是换成 icon 入口。
          embed 模式外层没有内边距，所以这里自带一点右下留白，别让问号贴着屏幕角。 */}
      <div className="olib-scope-bar">
        <CalcChainNote
          trigger="icon"
          title={t('olibTopo.note.title')}
          sections={[
            { title: t('olibTopo.note.sec.source'), lines: [t('olibTopo.note.source.1'), t('olibTopo.note.source.2')] },
            { title: t('olibTopo.note.sec.coords'), lines: [t('olibTopo.note.coords.1'), t('olibTopo.note.coords.2')] },
            { title: t('olibTopo.note.sec.core'), lines: [t('olibTopo.note.core.1'), t('olibTopo.note.core.2')] },
            { title: t('olibTopo.note.sec.limits'), lines: [t('olibTopo.note.limits.1')] },
          ]}
        />
      </div>
    </div>
  )
}
