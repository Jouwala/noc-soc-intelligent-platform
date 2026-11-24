/* 选中「站点类别」后叠加在 GIS 地图上的站点圈（2026-11-23，?page=gis）
 *
 * 与 `personmap/SiteClassLayer` 画的是同一批站点（同一个 `/gis/personnel/
 * site-classes` 响应），但**呈现方式不同，这是有意的**：
 *   · 人员页：底色上本来没有站点，类别站点就是主角 → 实心圆点；
 *   · GIS 页：底图上**已经画了全部站点**，颜色表示告警状态。再画实心圆会把
 *     告警色盖掉，同一批站点在图上就有了两种读法。故这里只画**外圈**：
 *     描边 = 类别色、填充几乎透明 —— 既标出「这一圈属于所选类别」，
 *     又不遮挡底下的告警状态。
 *
 * **层级**：挂 `siteClassPane`(660)，高于站点圆点(650)、低于县名标注(670) ——
 * 圈只是标注，不该压住县名与弹窗。
 *
 * **不响应鼠标**（`interactive={false}`）：圈盖在站点圆点之上，若可交互会
 * 把「点站点开下钻抽屉」吞掉；关掉后点击直接穿透到下面的站点圆点。
 *
 * **默认一个都不画**：`sites` 为空（图例一项都没选）时直接返回 null，
 * 与人员页「默认不显示，点击才显示」的需求一致。
 */
import { CircleMarker } from '../../components/LeafletMap'
import type { SiteClassSite } from '../personmap/types'

export function SiteClassSitesLayer({
  sites, colorOf,
}: {
  /** 已按图例选中项过滤好的站点集合（没选任何项时为空数组） */
  sites: SiteClassSite[]
  /** 站点 → 颜色（取第一个激活分组的类别色，与图例色块同源；
   *  由 `useSiteClasses().colorOf` 提供，本组件不自己算） */
  colorOf: (s: SiteClassSite) => string
}) {
  if (!sites.length) return null
  return (
    <>
      {sites.map(s => {
        const color = colorOf(s)
        return (
          /* `gis-class-site`：专属类，供自动化脚本统计「图例筛出来的圈」
             （pane 里还有站点圆点与人员图标，靠颜色区分太脆弱）。 */
          <CircleMarker
            key={`gis-cls-${s.site_id}`}
            pane="siteClassPane"
            center={[s.lat, s.lng]}
            radius={9}
            color={color}
            weight={2}
            opacity={0.95}
            fillColor={color}
            fillOpacity={0.12}
            className="gis-class-site"
            interactive={false}
          />
        )
      })}
    </>
  )
}
