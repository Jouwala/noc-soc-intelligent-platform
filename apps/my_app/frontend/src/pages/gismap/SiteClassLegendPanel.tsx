/* GIS 页右上角的「站点类别」浮层（2026-11-23 新增）
 *
 * **需求**（用户原话）：「页面 ?page=gis 的 Legend 里面的 Site Class 也添加
 * ?page=person 的 site class 里面的所有内容」；做法选定「整块复用人员页那张
 * 『站点类别』面板（默认全不选，选中才在地图上叠加显示）」。
 *
 * **刻意不复制一套**：数据（`personmap/useSiteClasses`）、面板
 * （`personmap/SiteClassLegend`）与类别配色（`personmap/siteClassColor`）都直接
 * 复用人员页的实现。这样两页的类别名、计数口径、选中语义（组内并集、组间交集）
 * 不可能各自漂移 —— 用户在两页看到的是同一份「站点类别」。
 * 本组件只负责「摆在 GIS 地图的哪里、占多大、怎么滚动」。
 *
 * **位置选右上角**：GIS 地图左缘已被 LAYERS（左上）与区域排名条占住，
 * 左下是站点状态图例（MapLegendOverlay），右下是 Leaflet 的版权条 ——
 * 只有右上角是空的。窄屏（门户内嵌约 570px 宽）下用 `58vw` 兜底，
 * 与 MapLegendOverlay / RegionRankingOverlay 的宽度写法一致。
 *
 * **⚠️ 为什么用 `createPortal` 挂进 Leaflet 容器，而不是像 MapLegendOverlay
 * 那样当地图卡片的兄弟节点**（2026-11-23 实测，必须照做）：
 *   门户（`apps/portal/*` 的 `MAP_STRIP_CSS`）在「只看地图」模式下会注入
 *   `main#main-content .ant-card-body:has(.leaflet-container) > *
 *    :not(.leaflet-container):not(.gis-legend-overlay){position:absolute;
 *    left:0;right:0;bottom:0;background:rgba(255,255,255,.88)!important;…}`
 *   —— 卡片 body 里任何「不是地图、也不含地图」的直接子元素都会被刷成
 *   通栏白底贴底块。既有图例靠白名单 `.gis-legend-overlay` 躲过这一条，
 *   本面板是新元素，若也当卡片子节点就得同步改 7 份门户静态壳
 *   （MsPortal / olbrportal / v1~v4 …，它们各自一份副本，改漏一份表现就不一致）。
 *   挂进 `.leaflet-container` 内部则不属于该选择器的目标，门户那边**不用改**。
 *   Leaflet 容器本身是 `position: relative`，绝对定位照常生效；
 *   层级与 LAYERS 面板同一套（同为容器内 z-index 1000 的 React 浮层）。
 *
 * **高度**：外层用 maxHeight + overflow:auto，面板自身的 maxHeight 传 `'none'`
 * 让滚动条只出现一次。人员页那边是 `calc(100vh - 120px)`，在 GIS 的地图卡片里
 * 不成立（卡片高度由 flex 分配，不是视口）。
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMap, isMapAlive } from '../../components/LeafletMap'
import { SiteClassLegend } from '../personmap/SiteClassLegend'
import type { useSiteClasses } from '../personmap/useSiteClasses'

/** 图例那一整套状态（取自 `useSiteClasses()`）。用 ReturnType 而不是再抄一遍
 *  字段清单 —— 加字段时不必两处改（与 personmap/MapTopRightOverlays 同写法）。 */
type SiteClassesState = ReturnType<typeof useSiteClasses>

/** 必须渲染在 `<MapContainer>` 内部（内部用 `useMap()` 取容器当挂载点）。 */
export function SiteClassLegendPanel({ state }: { state: SiteClassesState }) {
  const map = useMap()
  /* 挂载点：Leaflet 的地图容器。写法与 gismap/LayerToggleControl.tsx 完全一致
     （那里解释了为什么「返回值必须是 portal，不能直接 return 绝对定位 div」）。 */
  const [host, setHost] = useState<HTMLElement | null>(null)

  useEffect(() => {
    if (!isMapAlive(map)) return
    setHost(map.getContainer())
  }, [map])

  if (!host) return null

  return createPortal(
    /* `gis-siteclass-overlay`：专属类，便于 index.css 限定样式与自动化脚本定位
       （与 .gis-legend-overlay / .gis-rank-overlay 同一约定）。 */
    <div
      className="gis-siteclass-overlay"
      style={{
        position: 'absolute',
        right: 10,
        top: 10,
        zIndex: 1000,
        width: 'min(58vw, 13.75rem)',
        maxHeight: 'calc(100% - 20px)',
        overflow: 'auto',
      }}
    >
      <SiteClassLegend
        groups={state.groups}
        loading={state.loading}
        error={state.error}
        selected={state.selected}
        onToggle={state.toggle}
        onClear={state.clear}
        shown={state.visibleSites.length}
        total={state.total}
        nf={state.nf}
        labelOf={state.labelOf}
        maxHeight="none"
      />
    </div>,
    host,
  )
}
