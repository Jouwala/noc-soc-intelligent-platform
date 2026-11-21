/**
 * PS CN KPI 分组看板（?page=ps_cn_kpi）。
 *
 * 页面主体在 pages/cnKpiSplit/CnKpiBoard.tsx（与 CS 页共用）；
 * 这里只钉死看板 id，便于 App.tsx 的懒加载与菜单一一对应。
 */
import CnKpiBoard from './cnKpiSplit/CnKpiBoard'

export default function PsCnKpi() {
  return <CnKpiBoard board="ps" />
}
