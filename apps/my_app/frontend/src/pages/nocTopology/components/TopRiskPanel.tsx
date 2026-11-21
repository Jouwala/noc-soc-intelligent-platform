/* ============================================================================
 * NOC 拓扑中心 —— 风险链路榜
 * ============================================================================
 * 排序规则（model.topRiskEdges）：
 *   先按状态严重度（down > critical > major > minor > unknown > ok），
 *   同级别再按 max(TX, RX) 降序。
 * 这样「断链」永远排在「90% 但 healthy」前面 —— 对 NOC 值班来说，
 * 断链是已经发生的事故，高负载只是风险。
 *
 * 没有流量数据的链路（OLIB 全部、IPRAN 未覆盖的 65%）也会出现（如果状态异常），
 * 但利用率写 N/A，不写 0.0%。
 * ========================================================================== */
import { WH } from '../../../theme'
import { useI18n } from '../../../i18n'
import { CardBase } from '../../../components/cards'
import { edgeUtilization } from '../model'
import { SOURCE_LABEL_KEY, STATUS_LABEL_KEY, statusColor } from '../registry'
import type { NocEdge } from '../types'

export interface TopRiskPanelProps {
  edges: NocEdge[]
  onSelect: (key: string) => void
}

function util(v: number | null): string {
  return v == null ? 'N/A' : `${v.toFixed(1)}%`
}

export default function TopRiskPanel({ edges, onSelect }: TopRiskPanelProps) {
  const { t } = useI18n()

  return (
    <CardBase
      style={{ flex: '0 1 auto', minHeight: 0, maxHeight: 368, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
      bodyStyle={{ flex: '1 1 0%', minHeight: 0, height: 'auto', overflow: 'auto', padding: 'var(--wh-space-sm) var(--wh-space-md)' }}
      title={t('nocTopo.risk.title', { n: edges.length })}
    >
      {edges.length === 0 && (
        <div style={{ fontSize: 12, color: WH.textDim }}>{t('nocTopo.risk.empty')}</div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {edges.map((e, i) => {
          const u = edgeUtilization(e)
          return (
            <button
              key={e.key}
              type="button"
              onClick={() => onSelect(e.key)}
              style={{
                display: 'flex', alignItems: 'center', gap: 7, width: '100%',
                background: 'transparent', border: 'none', borderBottom: `1px solid ${WH.cardBorder}`,
                padding: '5px 2px', cursor: 'pointer', textAlign: 'left', color: WH.text,
              }}
            >
              <span style={{ fontSize: 10, color: WH.textDim, width: 14, flexShrink: 0 }}>{i + 1}</span>
              <span style={{ width: 7, height: 7, borderRadius: 4, background: statusColor(e.status), flexShrink: 0 }} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{
                  display: 'block', fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {e.sourceName} ↔ {e.targetName}
                </span>
                <span style={{ display: 'block', fontSize: 10, color: WH.textDim }}>
                  {t(SOURCE_LABEL_KEY[e.source])} · {t(STATUS_LABEL_KEY[e.status])}
                </span>
              </span>
              <span style={{
                fontSize: 12, fontWeight: 600, width: 52, textAlign: 'right', flexShrink: 0,
                color: u == null ? WH.textDim : WH.text,
              }}>
                {util(u)}
              </span>
            </button>
          )
        })}
      </div>
    </CardBase>
  )
}
