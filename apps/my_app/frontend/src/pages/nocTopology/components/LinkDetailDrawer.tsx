/* ============================================================================
 * NOC 拓扑中心 —— 链路详情抽屉
 * ============================================================================
 * 一条链路要能回答：两端是谁、什么介质、多大带宽、TX/RX 各是多少、
 * 有没有流量数据、端口明细是什么、历史趋势在哪。
 *
 * 三条口径纪律：
 *   · **TX / RX 分开显示**，并注明方向（TX = 源设备→宿设备），不写平均值；
 *   · **没有流量数据就写 N/A**，不显示 0.0%（OLIB 全长这样、IPRAN 只覆盖 35%）；
 *   · IPRAN 没有历史接口 → 趋势区明确写「无历史接口」，不画伪曲线。
 * ========================================================================== */
import { Alert, Drawer } from 'antd'

import { WH } from '../../../theme'
import { useI18n } from '../../../i18n'
import {
  MEDIUM_LABEL_KEY, SOURCE_LABEL_KEY, STATUS_LABEL_KEY, statusColor,
} from '../registry'
import type { NocEdge, NocNode } from '../types'
import { Field, SectionTitle } from './DetailField'
import TrafficTrendPanel from './TrafficTrendPanel'

export interface LinkDetailDrawerProps {
  open: boolean
  edge: NocEdge | null
  sourceNode: NocNode | null
  targetNode: NocNode | null
  onClose: () => void
  onSelectNode: (key: string) => void
}

function bw(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return v >= 1000 ? `${(v / 1000).toFixed(2)} Gbps` : `${v.toFixed(1)} Mbps`
}

function util(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return 'N/A'
  return `${v.toFixed(2)}%`
}

export default function LinkDetailDrawer({
  open, edge, sourceNode, targetNode, onClose, onSelectNode,
}: LinkDetailDrawerProps) {
  const { t } = useI18n()

  return (
    <Drawer
      placement="right"
      width="min(95vw, 42rem)"
      open={open && !!edge}
      onClose={onClose}
      destroyOnClose
      title={edge
        ? `${t('nocTopo.drawer.link')} · ${edge.sourceName} ↔ ${edge.targetName}`
        : t('nocTopo.drawer.link')}
    >
      {edge && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span style={{ width: 10, height: 10, borderRadius: 5, background: statusColor(edge.status) }} />
            <span style={{ fontSize: 13, color: statusColor(edge.status) }}>{t(STATUS_LABEL_KEY[edge.status])}</span>
            {edge.nativeStatus && (
              <span style={{ fontSize: 11, color: WH.textDim }}>native: {edge.nativeStatus}</span>
            )}
            <span style={{ fontSize: 12, color: WH.textDim }}>{t(SOURCE_LABEL_KEY[edge.source])}</span>
          </div>

          <Field label={t('nocTopo.detail.endpointA')}>
            <button type="button" onClick={() => onSelectNode(edge.sourceNode)}
              style={{ background: 'none', border: 'none', padding: 0, color: WH.teal, cursor: 'pointer', fontSize: 12 }}>
              {edge.sourceName}
            </button>
            {sourceNode?.ip && <span style={{ color: WH.textDim }}> · {sourceNode.ip}</span>}
          </Field>
          <Field label={t('nocTopo.detail.endpointB')}>
            <button type="button" onClick={() => onSelectNode(edge.targetNode)}
              style={{ background: 'none', border: 'none', padding: 0, color: WH.teal, cursor: 'pointer', fontSize: 12 }}>
              {edge.targetName}
            </button>
            {targetNode?.ip && <span style={{ color: WH.textDim }}> · {targetNode.ip}</span>}
          </Field>
          <Field label={t('nocTopo.detail.medium')}>{t(MEDIUM_LABEL_KEY[edge.medium])}</Field>
          <Field label={t('nocTopo.detail.capacity')}>{bw(edge.capacityMbps)}</Field>
          <Field label={t('nocTopo.detail.planned')}>{edge.planned ? t('user.yes') : t('user.no')}</Field>
          <Field label={t('nocTopo.detail.schematic')}>{edge.schematic ? t('user.yes') : t('user.no')}</Field>

          <SectionTitle>{t('nocTopo.detail.utilization')}</SectionTitle>
          <div style={{ fontSize: 11, color: WH.textDim, marginBottom: 4 }}>{t('nocTopo.detail.directionHint')}</div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 11, color: WH.textDim }}>TX · {edge.sourceName} → {edge.targetName}</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: edge.txUtilization == null ? WH.textDim : WH.text }}>
                {util(edge.txUtilization)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: WH.textDim }}>RX · {edge.targetName} → {edge.sourceName}</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: edge.rxUtilization == null ? WH.textDim : WH.text }}>
                {util(edge.rxUtilization)}
              </div>
            </div>
          </div>

          {!edge.trafficAvailable && (
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 10 }}
              message={t('nocTopo.detail.noTraffic')}
            />
          )}
          {edge.schematic && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 10 }}
              message={t('nocTopo.detail.schematicNote')}
            />
          )}

          {!!edge.checkpoints?.length && (
            <>
              <SectionTitle>{t('nocTopo.detail.ports')}</SectionTitle>
              <div style={{ fontSize: 11 }}>
                <div style={{ display: 'flex', gap: 8, color: WH.textDim, padding: '2px 0', borderBottom: `1px solid ${WH.cardBorder}` }}>
                  <span style={{ flex: 1, minWidth: 0 }}>{t('nocTopo.detail.port')}</span>
                  <span style={{ width: 56, textAlign: 'right' }}>TX</span>
                  <span style={{ width: 56, textAlign: 'right' }}>RX</span>
                </div>
                {edge.checkpoints.map((c, i) => (
                  <div key={`${c.port}-${i}`} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: `1px solid ${WH.cardBorder}` }}>
                    <span style={{ flex: 1, minWidth: 0, color: WH.text, wordBreak: 'break-all' }}>
                      {c.port}{c.peerPort ? ` ↔ ${c.peerPort}` : ''}
                    </span>
                    <span style={{ width: 56, textAlign: 'right', color: WH.textMuted }}>{util(c.txUtilization)}</span>
                    <span style={{ width: 56, textAlign: 'right', color: WH.textMuted }}>{util(c.rxUtilization)}</span>
                  </div>
                ))}
              </div>
            </>
          )}

          {!!edge.ports?.length && !edge.checkpoints?.length && (
            <>
              <SectionTitle>{t('nocTopo.detail.ports')}</SectionTitle>
              <div style={{ fontSize: 11, color: WH.textMuted, lineHeight: 1.7, wordBreak: 'break-all' }}>
                {edge.ports.map((p, i) => <div key={i}>{p}</div>)}
              </div>
            </>
          )}

          <SectionTitle>{t('nocTopo.detail.history')}</SectionTitle>
          <TrafficTrendPanel history={edge.history} />
        </div>
      )}
    </Drawer>
  )
}
