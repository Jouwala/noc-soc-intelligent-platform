/* GIS 图例里嵌入的「站点类别」内容（2026-11-24 改位置）
 *
 * **需求演进**：
 *   · 2026-11-23 用户要求「?page=gis 的 Legend 里面的 Site Class 也添加
 *     ?page=person 的 site class 里面的所有内容」→ 当时做成了右上角一张独立浮层；
 *   · 2026-11-24 用户纠正：「是把 Site Classes 放到 Legend 里面的 Site class 里面」
 *     → 即内容必须落在左下角图例的「站点等级 / Site Class」组内，而不是另开一块。
 *
 * 本组件只做一件事：把人员页那张面板（`personmap/SiteClassLegend`）以 `bare`
 * 模式渲染出来，交给 `MapLegendOverlay` 的组 `node` 插槽。数据（`useSiteClasses`）、
 * 面板逻辑、配色（`siteClassColor`）仍是同一份实现，两页不会各自漂移。
 *
 * **默认收起，组内一行「站点类别 ▸」就是开关**（有选中时行尾显示「已显示 N / total」）：
 * 四组 11 个取值全展开会把图例撑到 600px，盖住左上的 LAYERS 与区域排名条
 * （2026-11-24 实测）。展开时：
 *   · 图例高度由 `MapLegendOverlay` 的 `maxHeight: calc(100% - 12rem)` 封顶
 *     （留出 LAYERS 的位置），超出部分在图例内部滚动；
 *   · 区域排名条由 index.css 的 `.gis-legend-overlay:has(button[aria-expanded='true'])
 *     ~ .gis-rank-overlay` 临时收起 —— 两块半透明浮层同处左缘，叠在一起既透字
 *     又会挡住类别行的点击。收起图例后排名条自动恢复。
 *
 * 原来那套 `createPortal` 挂进 Leaflet 容器的定位壳已随位置变更删除 ——
 * 内容现在跟着图例走（图例本来就是地图卡片 body 的兄弟节点，且已在门户
 * `MAP_STRIP_CSS` 的白名单 `.gis-legend-overlay` 里）。
 *
 * 默认仍是**一项都不选**（沿用人员页语义）：没选时地图上不画任何类别圈。
 */
import { SiteClassLegend } from '../personmap/SiteClassLegend'
import type { useSiteClasses } from '../personmap/useSiteClasses'

/** 图例那一整套状态（取自 `useSiteClasses()`）。用 ReturnType 而不是再抄一遍
 *  字段清单 —— 加字段时不必两处改（与 personmap/MapTopRightOverlays 同写法）。 */
type SiteClassesState = ReturnType<typeof useSiteClasses>

export function SiteClassLegendSection({ state }: { state: SiteClassesState }) {
  return (
    <SiteClassLegend
      bare
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
    />
  )
}
