/* ============================================================================
 * NOC 拓扑中心 —— 来源健康面板
 * ============================================================================
 * 每个来源单独一行：状态 / 设备数 / 链路数 / 采样时刻 / 覆盖率 / 告警条数。
 * 这是「一个数据源断线不能让整张图看起来都坏了」的落点：
 *   某个来源 unavailable 时只把那一行标红，另外两行照常显示 Connected。
 *
 * 状态取自上而下的适配结果（SourceMetadata.state）：
 *   Connected / Partial / Stale / Unavailable / Static / Loading
 * 其中 Static 专指 OLIB —— 静态资产，不参与「实时性」判断。
 * ========================================================================== */
import { Tooltip } from 'antd'
import { WarningOutlined } from '@ant-design/icons'

import { WH } from '../../../theme'
import { useI18n } from '../../../i18n'
import { CardBase } from '../../../components/cards'
import { formatDateTime } from '../../../datetime'
import { SOURCE_DESC_KEY, SOURCE_LABEL_KEY, STATE_LABEL_KEY, stateColor } from '../registry'
import type { SourceMetadata } from '../types'

function pct(v: number | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return `${(v * 100).toFixed(v >= 0.995 ? 0 : 1)}%`
}

export default function SourceHealthPanel({ sources }: { sources: SourceMetadata[] }) {
  const { t } = useI18n()

  return (
    <CardBase
      title={t('nocTopo.health.title')}
      bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {sources.map((s) => {
          const color = stateColor(s.state)
          return (
            <div key={s.source} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                <span style={{
                  width: 8, height: 8, borderRadius: 4, background: color,
                  display: 'inline-block', flexShrink: 0,
                }} />
                <span style={{ fontSize: 12, fontWeight: 600, color: WH.text, whiteSpace: 'nowrap' }}>
                  {t(SOURCE_LABEL_KEY[s.source])}
                </span>
                <span style={{ fontSize: 11, color, whiteSpace: 'nowrap' }}>
                  {t(STATE_LABEL_KEY[s.state])}
                </span>
                <span style={{ marginLeft: 'auto', fontSize: 11, color: WH.textDim, whiteSpace: 'nowrap' }}>
                  {t('nocTopo.health.counts', { n: s.nodeCount, m: s.edgeCount })}
                </span>
              </div>

              <div style={{ fontSize: 11, color: WH.textDim, lineHeight: 1.5 }}>
                {s.sampledAt
                  ? t('nocTopo.health.sampled', { at: formatDateTime(s.sampledAt) })
                  : s.fetchedAt
                    ? t('nocTopo.health.updated', { at: formatDateTime(s.fetchedAt) })
                    : t(SOURCE_DESC_KEY[s.source])}
                {s.coverage != null && (
                  <span style={{ marginLeft: 8 }}>{t('nocTopo.health.coverage', { v: pct(s.coverage) })}</span>
                )}
                {s.granularity && (
                  <span style={{ marginLeft: 8 }}>{s.granularity}</span>
                )}
              </div>

              {/* 覆盖不足是「异常」不是「解释」：常驻，不折叠 */}
              {s.warnings.length > 0 && (
                <Tooltip
                  title={<div style={{ fontSize: 11, lineHeight: 1.6 }}>{s.warnings.map((w, i) => <div key={i}>{w}</div>)}</div>}
                  placement="left"
                >
                  <div style={{ fontSize: 11, color: WH.warn, display: 'flex', alignItems: 'center', gap: 4 }}>
                    <WarningOutlined />
                    {s.warnings[0]}
                    {s.warnings.length > 1 && <span>+{s.warnings.length - 1}</span>}
                  </div>
                </Tooltip>
              )}

              {s.state === 'unavailable' && s.error && (
                <div style={{ fontSize: 11, color: WH.danger, wordBreak: 'break-all' }}>{s.error}</div>
              )}

              {s.stats?.skippedLinks ? (
                <div style={{ fontSize: 11, color: WH.textDim }}>
                  {t('nocTopo.health.skipped', { n: s.stats.skippedLinks })}
                </div>
              ) : null}
            </div>
          )
        })}
      </div>
    </CardBase>
  )
}
