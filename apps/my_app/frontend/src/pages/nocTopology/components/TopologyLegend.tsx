/* ============================================================================
 * NOC 拓扑中心 —— 图例
 * ============================================================================
 * 图例必须回答四个问题，否则用户看不懂画布：
 *   1. 形状 = 什么设备？（核心/汇聚/交换机/接入/防火墙/运营商/微波/站点）
 *   2. 颜色 = 什么状态？（含 MW 四级与 N/A、静态、规划）
 *   3. 线型 = 什么链路？（光纤/微波/示意/规划/故障/无流量）
 *   4. 流量色阶 = 哪一档？（只在流量视图显示 8 档）
 *
 * 另有一条常驻说明：OLIB 的线是**连接关系示意**，不是光缆路由。
 * ========================================================================== */
import { type ReactNode } from 'react'
import { Space } from 'antd'

import { WH } from '../../../theme'
import { useI18n } from '../../../i18n'
import { renderRich } from '../../../utils/richText'
import { DEVICE_LABEL_KEY, SOURCE_LABEL_KEY, STATUS_LABEL_KEY, TRAFFIC_BINS, TRAFFIC_NA_COLOR, sourceColor, statusColor } from '../registry'
import type { NocDeviceType, NocStatus, NocView } from '../types'

const DEVICES: NocDeviceType[] = ['core', 'aggregation', 'switch', 'access', 'firewall', 'isp', 'microwave', 'site']
const STATUSES: NocStatus[] = ['ok', 'minor', 'major', 'critical', 'down', 'unknown', 'static', 'planned']

function Glyph({ type }: { type: NocDeviceType }) {
  const c = WH.textMuted
  const r = 5
  switch (type) {
    case 'core':
      return (
        <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true">
          <polygon points="0,-5 4.3,-2.5 4.3,2.5 0,5 -4.3,2.5 -4.3,-2.5" fill="none" stroke={c} strokeWidth="1.2" />
        </svg>
      )
    case 'aggregation':
      return <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true"><rect x={-r} y={-r} width={r * 2} height={r * 2} rx={1.8} fill="none" stroke={c} strokeWidth="1.2" /></svg>
    case 'switch':
      return <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true"><polygon points={`0,${-r} ${r},0 0,${r} ${-r},0`} fill="none" stroke={c} strokeWidth="1.2" /></svg>
    case 'firewall':
      return (
        <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true">
          <path d="M0 -5 L5 -2.2 L5 1.8 Q0 6.2 -5 1.8 L-5 -2.2 Z" fill="none" stroke={c} strokeWidth="1.2" />
        </svg>
      )
    case 'isp':
      return (
        <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true">
          <path d="M-5 1.2 q0 -2.8 2.5 -2.5 q0.8 -3.5 4.3 -2.2 q2.8 -0.8 3 2.2 q2.8 0.3 0.3 2.8 Z" fill="none" stroke={c} strokeWidth="1" />
        </svg>
      )
    case 'microwave':
      return <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true"><polygon points="0,-5 4.8,3.8 -4.8,3.8" fill="none" stroke={c} strokeWidth="1.2" /></svg>
    default:
      return <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true"><circle r={r * 0.85} fill="none" stroke={c} strokeWidth="1.2" /></svg>
  }
}

function Item({ children }: { children: ReactNode }) {
  return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>{children}</span>
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, minWidth: 0 }}>
      <span style={{ color: WH.textDim, fontSize: 11, whiteSpace: 'nowrap', lineHeight: '18px' }}>{title}</span>
      <Space size={[12, 4]} wrap style={{ fontSize: 11 }}>{children}</Space>
    </div>
  )
}

export default function TopologyLegend({ view }: { view: NocView }) {
  const { t } = useI18n()

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '2px 2px 4px' }}>
      <Section title={t('nocTopo.legend.devices')}>
        {DEVICES.map((d) => (
          <Item key={d}><Glyph type={d} /><span style={{ color: WH.textMuted }}>{t(DEVICE_LABEL_KEY[d])}</span></Item>
        ))}
      </Section>

      <Section title={t('nocTopo.legend.status')}>
        {STATUSES.map((s) => (
          <Item key={s}>
            <span style={{ width: 9, height: 9, borderRadius: 5, background: statusColor(s), display: 'inline-block' }} />
            <span style={{ color: WH.textMuted }}>{t(STATUS_LABEL_KEY[s])}</span>
          </Item>
        ))}
      </Section>

      <Section title={t('nocTopo.legend.source')}>
        {(['ipran', 'mw', 'olib'] as const).map((s) => (
          <Item key={s}>
            <span style={{
              width: 10, height: 10, borderRadius: 5, display: 'inline-block',
              border: `2px solid ${sourceColor(s)}`,
            }} />
            <span style={{ color: WH.textMuted }}>{t(SOURCE_LABEL_KEY[s])}</span>
          </Item>
        ))}
      </Section>

      <Section title={t('nocTopo.legend.links')}>
        <Item><svg width="26" height="8" aria-hidden="true"><line x1="0" y1="4" x2="26" y2="4" stroke={WH.textMuted} strokeWidth="1.6" /></svg><span style={{ color: WH.textMuted }}>{t('nocTopo.medium.fiber')}</span></Item>
        <Item><svg width="26" height="8" aria-hidden="true"><line x1="0" y1="4" x2="26" y2="4" stroke={WH.textMuted} strokeWidth="1.6" strokeDasharray="2 3" /></svg><span style={{ color: WH.textMuted }}>{t('nocTopo.medium.schematic')}</span></Item>
        <Item><svg width="26" height="8" aria-hidden="true"><line x1="0" y1="4" x2="26" y2="4" stroke={WH.textDim} strokeWidth="1.6" strokeDasharray="5 4" /></svg><span style={{ color: WH.textMuted }}>{t('nocTopo.status.planned')}</span></Item>
        <Item><svg width="26" height="8" aria-hidden="true"><line x1="0" y1="4" x2="26" y2="4" stroke={WH.danger} strokeWidth="1.6" strokeDasharray="6 4" /></svg><span style={{ color: WH.textMuted }}>{t('nocTopo.status.down')}</span></Item>
        <Item><svg width="26" height="8" aria-hidden="true"><line x1="0" y1="4" x2="26" y2="4" stroke={TRAFFIC_NA_COLOR} strokeWidth="1.6" strokeDasharray="4 4" /></svg><span style={{ color: WH.textMuted }}>{t('nocTopo.legend.trafficNa')}</span></Item>
      </Section>

      {view === 'traffic' && (
        <Section title={t('nocTopo.legend.traffic')}>
          {TRAFFIC_BINS.map((b) => (
            <Item key={b.label}>
              <span style={{ width: 14, height: 8, background: b.color, display: 'inline-block', border: `1px solid ${WH.cardBorder}` }} />
              <span style={{ color: WH.textMuted }}>{b.label}</span>
            </Item>
          ))}
        </Section>
      )}
      {view === 'traffic' && (
        <div style={{ fontSize: 11, color: WH.textDim }}>{t('nocTopo.legend.direction')}</div>
      )}

      <div style={{ fontSize: 11, color: WH.textDim, lineHeight: 1.6 }}>
        {renderRich(t('nocTopo.legend.schematicNote'))}
      </div>
    </div>
  )
}
