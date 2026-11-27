/* ============================================================================
 * VmaxEvidenceDrawer —— 原始证据抽屉（CCA 工作台，懒挂载）
 * ============================================================================
 * 首屏只取诊断 + CCA + flow 摘要；用户点「查看证据」才挂载对应原始面板，
 * 关闭即卸载。抽屉作用域独立 Provider，值由页面 query 派生。
 *
 * 2026-11-26 收编（整改清单 P1-4 / P2-7）：
 *   - 'xdr' 与 'records' 合并成一个「原始信令 XDR」抽屉，内分两个页签
 *     （合并表：全部接口流水 / 分表：UEMR/UU/X2…），默认页签随入口 kind；
 *     此前是两个独立入口、两套下拉，查的是同一批类型。
 *   - 'flow' 由页面拦截为「滚动到区域 2 的流量卡」，不再打开抽屉 ——
 *     抽屉里的流量面板与页面内嵌那张是同一份数据（这里仍保留分支作兜底）。
 *   - XDR 面板的 URL 参数改用 cca 前缀（ccaMxSvc / ccaRtSvc / ccaRtTable），
 *     与旧页 mxSvc/rtSvc/rtTable 不再互踩。
 *
 * 并行取数靠 Provider 的 ctx.query.token 触发（见 VmaxXdrPanel）；
 * 页签默认懒渲染，切到哪个页签才发哪张表的请求。
 * ========================================================================== */

import { Drawer, Tabs } from 'antd'

import { useI18n } from '../../i18n'
import { VmaxWindowContext, type VmaxWindowValue } from '../../pages/vmaxWindowContext'
import { PAGE_RETENTION_HOURS } from '../../pages/vmaxWindow'
import type { RadioAnalysis } from '../../pages/vmaxDiagnosisRules'
import type { CcaResult } from '../VmaxCcaCards'
import type { FlowData } from '../VmaxFlowCategoryTabs'
import type { EvidenceKind } from '../../lib/vmaxWorkspaceRules'
import VmaxXdrPanel from '../VmaxXdrPanel'
import VmaxXdrRecordsPanel from '../VmaxXdrRecordsPanel'
import VmaxRadioPanel from '../VmaxRadioPanel'
import VmaxFlowCategoryTabs from '../VmaxFlowCategoryTabs'
import VmaxCcaEvidencePanel from './VmaxCcaEvidencePanel'

export type { EvidenceKind }

/** CCA 工作台专用 URL 参数名（与旧页隔离） */
const MERGED_KEYS = { svc: 'ccaMxSvc' }
const TABLE_KEYS = { svc: 'ccaRtSvc', table: 'ccaRtTable' }

export default function VmaxEvidenceDrawer({
  kind, open, onClose, msisdn, ctx, radio, diagLoading, flowXdr, cca,
}: {
  kind: EvidenceKind | null
  open: boolean
  onClose: () => void
  msisdn: string
  ctx: VmaxWindowValue
  radio: RadioAnalysis | null
  diagLoading: boolean
  flowXdr: FlowData | null
  /** CCA 查询结果（cca 面板用；页面已有，不重取） */
  cca: CcaResult | null
}) {
  const { t } = useI18n()
  const isSignal = kind === 'xdr' || kind === 'records'
  const titleKey = isSignal ? 'vmaxq.ws.drawer.xdr'
    : kind === 'radio' ? 'vmaxq.ws.drawer.radio'
    : kind === 'flow' ? 'vmaxq.ws.drawer.flow'
    : kind === 'cca' ? 'vmaxq.ws.drawer.cca'
    : 'vmaxq.ws.drawer.title'

  const value: VmaxWindowValue = {
    ...ctx,
    retentionHours: PAGE_RETENTION_HOURS,
  }

  return (
    <Drawer
      placement="right"
      width="min(95vw, 72rem)"
      open={open}
      onClose={onClose}
      title={t(titleKey)}
      destroyOnHidden
    >
      {/* 抽屉内独立 Provider：子面板靠 ctx.query.token 决定并行取数;
          关闭即 destroyOnHidden 卸载，不再发请求。 */}
      <VmaxWindowContext.Provider value={value}>
        {isSignal && (
          <Tabs
            size="small"
            defaultActiveKey={kind === 'records' ? 'records' : 'xdr'}
            items={[
              {
                key: 'xdr',
                label: t('vmaxq.ws.drawer.tabMerged'),
                children: <VmaxXdrPanel msisdn={msisdn} paramKeys={MERGED_KEYS} />,
              },
              {
                key: 'records',
                label: t('vmaxq.ws.drawer.tabTable'),
                children: <VmaxXdrRecordsPanel msisdn={msisdn} paramKeys={TABLE_KEYS} />,
              },
            ]}
          />
        )}
        {kind === 'radio' && (
          <VmaxRadioPanel analysis={radio} loading={diagLoading} msisdn={msisdn} />
        )}
        {kind === 'flow' && <VmaxFlowCategoryTabs flow={flowXdr} />}
        {kind === 'cca' && <VmaxCcaEvidencePanel result={cca} />}
      </VmaxWindowContext.Provider>
    </Drawer>
  )
}
