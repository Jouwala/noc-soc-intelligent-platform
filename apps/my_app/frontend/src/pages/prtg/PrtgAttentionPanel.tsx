/* ============================================================================
 * 「需要关注」面板：按严重度列出最多约 8 条链路，点击打开详情。
 * 口径只做选取（status / uptime_pct / down_count_24h / errors|discards），不重算。
 * ========================================================================== */

import { Empty } from 'antd'

import { useI18n } from '../../i18n'
import { WH } from '../../theme'
import { CardBase } from '../../components/cards'
import { ExplainIcon } from '../../components/MetricExplain'
import {
  AttentionItem, AttentionKind, NA, PrtgLink, cleanSensorName,
  fmtNum, pickAttentionItems, shortDeviceName,
} from './prtgModels'

function kindDotColor(kind: AttentionKind): string {
  switch (kind) {
    case 'down': return WH.danger
    case 'unusual': return WH.warn
    case 'low_uptime': return WH.warn
    case 'down_24h': return WH.danger
    case 'errors': return WH.warn
    default: return WH.textMuted
  }
}

function reasonText(item: AttentionItem, t: (k: string, p?: Record<string, string | number>) => string): string {
  const { kind, link } = item
  switch (kind) {
    case 'down':
      return t('prtg.attention.reason.down')
    case 'unusual': {
      const dir = link.unusual_reason?.direction
      if (dir === 'high') return t('prtg.attention.reason.unusualHigh')
      if (dir === 'low') return t('prtg.attention.reason.unusualLow')
      return t('prtg.attention.reason.unusual')
    }
    case 'low_uptime':
      return t('prtg.attention.reason.lowUptime', { value: fmtNum(link.uptime_pct, 3) })
    case 'down_24h':
      return t('prtg.attention.reason.down24h', { n: link.down_count_24h ?? NA })
    case 'errors':
      return t('prtg.attention.reason.errors', {
        errors: `${fmtNum(link.errors_in)} / ${fmtNum(link.errors_out)}`,
        discards: `${fmtNum(link.discards_in)} / ${fmtNum(link.discards_out)}`,
      })
    default:
      return NA
  }
}

export interface PrtgAttentionPanelProps {
  links: PrtgLink[]
  onSelect: (link: PrtgLink, at?: { x: number; y: number }) => void
  /** 悬停联动拓扑高亮（sensor_id）；离开时传 null */
  onHighlight?: (sensorId: number | null) => void
}

export default function PrtgAttentionPanel({ links, onSelect, onHighlight }: PrtgAttentionPanelProps) {
  const { t } = useI18n()
  const items = pickAttentionItems(links, 8)

  return (
    <CardBase
      title={<>{t('prtg.attention.title')} <ExplainIcon text={t('prtg.attention.explain')} /></>}
      bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}
    >
      {items.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t('prtg.attention.allOk')}
          style={{ margin: '12px 0' }}
        />
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {items.map(item => {
            const name = cleanSensorName(item.link.sensor_name)
            const device = shortDeviceName(item.link.device_name)
            return (
              <li key={item.link.sensor_id}>
                <button
                  type="button"
                  onClick={e => onSelect(item.link, { x: e.clientX, y: e.clientY })}
                  onMouseEnter={() => onHighlight?.(item.link.sensor_id)}
                  onMouseLeave={() => onHighlight?.(null)}
                  onFocus={() => onHighlight?.(item.link.sensor_id)}
                  onBlur={() => onHighlight?.(null)}
                  title={item.link.sensor_name || undefined}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 8,
                    width: '100%',
                    textAlign: 'left',
                    padding: '6px 8px',
                    borderRadius: 6,
                    border: `1px solid ${WH.cardBorder}`,
                    background: 'transparent',
                    color: WH.text,
                    cursor: 'pointer',
                  }}
                >
                  <span
                    aria-hidden
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      marginTop: 6,
                      flexShrink: 0,
                      background: kindDotColor(item.kind),
                    }}
                  />
                  <span style={{ minWidth: 0, flex: 1 }}>
                    <span style={{ display: 'block', fontWeight: 560, lineHeight: 1.3 }}>
                      {name}
                    </span>
                    <span style={{ display: 'block', fontSize: 12, color: WH.textMuted, lineHeight: 1.35 }}>
                      {device !== NA ? `${device} · ` : ''}{reasonText(item, t)}
                    </span>
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </CardBase>
  )
}
