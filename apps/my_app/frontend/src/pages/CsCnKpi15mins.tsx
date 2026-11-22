/**
 * CS CN KPI 15 分钟看板（?page=cs_cn_kpi_15mins）。
 *
 * 页面主体在 pages/cnKpi15m/CnKpi15mBoard.tsx（与 PS 页共用）；
 * 这里只钉死看板 id，便于 App.tsx 的懒加载与菜单一一对应。
 */
import CnKpi15mBoard from './cnKpi15m/CnKpi15mBoard'

export default function CsCnKpi15mins() {
  return <CnKpi15mBoard board="cs" />
}
