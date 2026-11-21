/* ============================================================================
 * NOC 拓扑中心 —— 设备详情抽屉
 * ============================================================================
 * 只展示**模型里已有的字段**，不在这里重算任何口径：
 *   来源身份（source + nativeId）、IP、站点、层级、设备类型、状态、告警计数、
 *   经纬度，以及当前图上与它相连的链路（点一条跳到链路详情）。
 * ========================================================================== */
import { Drawer } from 'antd'

import { WH } from '../../../theme'
import { useI18n } from '../../../i18n'
import {
  DEVICE_LABEL_KEY, MEDIUM_LABEL_KEY, SOURCE_LABEL_KEY, STATUS_LABEL_KEY, statusColor,
} from '../registry'
import type { NocEdge, NocNode } from '../types'
import { Field, SectionTitle } from './DetailField'

export interface DeviceDetailDrawerProps {
  open: boolean
  node: NocNode | null
  incident: NocEdge[]
  nodesByKey: Map<string, NocNode>
  onClose: () => void
  onOpenEdge: (key: string) => void
}

function coord(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return v.toFixed(5)
}

export default function DeviceDetailDrawer({
  open, node, incident, nodesByKey, onClose, onOpenEdge,
}: DeviceDetailDrawerProps) {
  const { t } = useI18n()

  return (
    <Drawer
      placement="right"
      width="min(95vw, 38rem)"
      open={open && !!node}
      onClose={onClose}
      destroyOnClose
      title={node ? `${t('nocTopo.drawer.device')} · ${node.name}` : t('nocTopo.drawer.device')}
    >
      {node && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span style={{ width: 10, height: 10, borderRadius: 5, background: statusColor(node.status) }} />
            <span style={{ fontSize: 13, color: statusColor(node.status) }}>{t(STATUS_LABEL_KEY[node.status])}</span>
            <span style={{ fontSize: 12, color: WH.textDim }}>{t(SOURCE_LABEL_KEY[node.source])}</span>
          </div>

          <Field label={t('nocTopo.detail.name')}>{node.name}</Field>
          <Field label={t('nocTopo.detail.nativeId')}>{node.nativeId}</Field>
          <Field label={t('nocTopo.detail.deviceType')}>{t(DEVICE_LABEL_KEY[node.deviceType])}</Field>
          <Field label={t('nocTopo.detail.source')}>{t(SOURCE_LABEL_KEY[node.source])}</Field>
          {node.ip && <Field label={t('nocTopo.detail.ip')}>{node.ip}</Field>}
          {node.siteName && <Field label={t('nocTopo.detail.site')}>{node.siteName}</Field>}
          {node.siteId && <Field label={t('nocTopo.detail.siteId')}>{node.siteId}</Field>}
          {node.layer && <Field label={t('nocTopo.detail.layer')}>{node.layer}</Field>}
          <Field label={t('nocTopo.detail.coords')}>
            {node.latitude == null || node.longitude == null
              ? t('nocTopo.detail.noGeo')
              : `${coord(node.latitude)}, ${coord(node.longitude)}`}
          </Field>
          <Field label={t('nocTopo.detail.degree')}>{node.degree}</Field>

          {node.alarms && (
            <>
              <SectionTitle>{t('nocTopo.detail.alarms')}</SectionTitle>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 12 }}>
                <span style={{ color: WH.sev.Critical }}>crit {node.alarms.crit}</span>
                <span style={{ color: WH.sev.Major }}>major {node.alarms.major}</span>
                <span style={{ color: WH.warn }}>minor {node.alarms.minor}</span>
                <span style={{ color: WH.textDim }}>warn {node.alarms.warn}</span>
              </div>
            </>
          )}

          <SectionTitle>{t('nocTopo.detail.edges', { n: incident.length })}</SectionTitle>
          {incident.length === 0 && (
            <div style={{ fontSize: 12, color: WH.textDim }}>{t('nocTopo.detail.noEdges')}</div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, maxHeight: '42vh', overflowY: 'auto' }}>
            {incident.map((e) => {
              const peerKey = e.sourceNode === node.key ? e.targetNode : e.sourceNode
              const peer = nodesByKey.get(peerKey)
              return (
                <button
                  key={e.key}
                  type="button"
                  onClick={() => onOpenEdge(e.key)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                    background: 'transparent', border: 'none', borderBottom: `1px solid ${WH.cardBorder}`,
                    padding: '5px 2px', cursor: 'pointer', textAlign: 'left', color: WH.text,
                  }}
                >
                  <span style={{ width: 7, height: 7, borderRadius: 4, background: statusColor(e.status), flexShrink: 0 }} />
                  <span style={{ fontSize: 12, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {peer?.name ?? (peerKey === e.targetNode ? e.targetName : e.sourceName)}
                  </span>
                  <span style={{ fontSize: 11, color: WH.textDim }}>{t(MEDIUM_LABEL_KEY[e.medium])}</span>
                  <span style={{ fontSize: 11, color: WH.textDim, width: 84, textAlign: 'right' }}>
                    {t('nocTopo.detail.txShort', { v: e.txUtilization == null ? 'N/A' : `${e.txUtilization.toFixed(1)}%` })}
                  </span>
                </button>
              )
            })}
          </div>
        </div>
      )}
    </Drawer>
  )
}
