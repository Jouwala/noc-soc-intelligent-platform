/* ============================================================================
 * location/siteColors.ts —— 位置页的站点配色（与 ?page=gis 同源）
 * ============================================================================
 *
 * 位置页没有图层开关，恒把 GIS 的四个站点图层全部打开；配色不另配一套：
 *   · 分档：gismap/types.ts::siteLegendKey(site, 'all', ALL_LAYERS)
 *   · 色值：gismap/types.ts::colorFor()
 * 两处共用本文件的 siteColor()，避免「列表色点」与「地图圆点」各算一遍而漂移。
 * ========================================================================== */
import type { LayerKey, Site } from '../gismap/types'
import { colorFor, siteLegendKey } from '../gismap/types'

/** GIS 的四个站点图层全开（位置页没有图层开关）。 */
export const ALL_LAYERS: Set<LayerKey> = new Set<LayerKey>(['rt_alarm', 'his_alarm', 'tt', 'wo'])

/** 站点圆点 / 列表色点的颜色（与 GIS 页同一规则、同一色值）。 */
export function siteColor(site: Site): string {
  return colorFor(siteLegendKey(site, 'all', ALL_LAYERS))
}
