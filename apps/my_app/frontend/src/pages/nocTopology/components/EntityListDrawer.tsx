/* ============================================================================
 * NOC 拓扑中心 —— 汇总数字的下钻清单
 * ============================================================================
 * 项目硬要求：看板上的每个数字都要能点开看到构成它的明细（AGENTS 三.1b）。
 * 这里承载「网络设备」「活动链路」两张卡：
 *   · 只渲染前 300 行，并在顶部常驻「仅显示前 300 / 共 N 条」——
 *     截断必须显式暴露，不能静默。
 *   · 行可点：设备 → 设备抽屉，链路 → 链路抽屉。
 * ========================================================================== */
import { Drawer } from 'antd'

import { WH } from '../../../theme'
import { useI18n } from '../../../i18n'
import { edgeUtilization } from '../model'
import {
  DEVICE_LABEL_KEY, SOURCE_LABEL_KEY, STATUS_LABEL_KEY, statusColor,
} from '../registry'
import type { NocEdge, NocNode } from '../types'

const LIMIT = 300

export interface EntityListDrawerProps {
  open: boolean
  kind: 'node' | 'edge'
  nodes: NocNode[]
  edges: NocEdge[]
  onClose: () => void
  onSelectNode: (key: string) => void
  onSelectEdge: (key: string) => void
}

export default function EntityListDrawer({
  open, kind, nodes, edges, onClose, onSelectNode, onSelectEdge,
}: EntityListDrawerProps) {
  const { t } = useI18n()
  const total = kind === 'node' ? nodes.length : edges.length
  const shownNodes = kind === 'node' ? nodes.slice(0, LIMIT) : []
  const shownEdges = kind === 'edge' ? edges.slice(0, LIMIT) : []

  return (
    <Drawer
      placement="right"
      width="min(95vw, 46rem)"
      open={open}
      onClose={onClose}
      destroyOnClose
      title={kind === 'node' ? t('nocTopo.list.nodesTitle') : t('nocTopo.list.edgesTitle')}
    >
      <div style={{ fontSize: 11, color: WH.textDim, marginBottom: 8 }}>
        {total > LIMIT
          ? t('nocTopo.list.truncated', { shown: LIMIT, total })
          : t('nocTopo.list.total', { total })}
      </div>

      {total === 0 && <div style={{ fontSize: 12, color: WH.textDim }}>{t('nocTopo.list.empty')}</div>}

      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {shownNodes.map((n) => (
          <button
            key={n.key}
            type="button"
            onClick={() => onSelectNode(n.key)}
            style={{
              display: 'flex', alignItems: 'center', gap: 8, width: '100%',
              background: 'transparent', border: 'none', borderBottom: `1px solid ${WH.cardBorder}`,
              padding: '5px 2px', cursor: 'pointer', textAlign: 'left', color: WH.text,
            }}
          >
            <span style={{ width: 7, height: 7, borderRadius: 4, background: statusColor(n.status), flexShrink: 0 }} />
            <span style={{ flex: 1, minWidth: 0, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {n.name}
            </span>
            <span style={{ fontSize: 11, color: WH.textDim, width: 88, flexShrink: 0 }}>
              {t(SOURCE_LABEL_KEY[n.source])}
            </span>
            <span style={{ fontSize: 11, color: WH.textDim, width: 96, flexShrink: 0 }}>
              {t(DEVICE_LABEL_KEY[n.deviceType])}
            </span>
            <span style={{ fontSize: 11, color: statusColor(n.status), width: 52, flexShrink: 0 }}>
              {t(STATUS_LABEL_KEY[n.status])}
            </span>
          </button>
        ))}

        {shownEdges.map((e) => {
          const u = edgeUtilization(e)
          return (
            <button
              key={e.key}
              type="button"
              onClick={() => onSelectEdge(e.key)}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                background: 'transparent', border: 'none', borderBottom: `1px solid ${WH.cardBorder}`,
                padding: '5px 2px', cursor: 'pointer', textAlign: 'left', color: WH.text,
              }}
            >
              <span style={{ width: 7, height: 7, borderRadius: 4, background: statusColor(e.status), flexShrink: 0 }} />
              <span style={{ flex: 1, minWidth: 0, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {e.sourceName} ↔ {e.targetName}
              </span>
              <span style={{ fontSize: 11, color: WH.textDim, width: 88, flexShrink: 0 }}>
                {t(SOURCE_LABEL_KEY[e.source])}
              </span>
              <span style={{ fontSize: 11, color: WH.textDim, width: 72, flexShrink: 0 }}>
                {t(STATUS_LABEL_KEY[e.status])}
              </span>
              <span style={{ fontSize: 11, color: u == null ? WH.textDim : WH.text, width: 56, textAlign: 'right', flexShrink: 0 }}>
                {u == null ? 'N/A' : `${u.toFixed(1)}%`}
              </span>
            </button>
          )
        })}
      </div>
    </Drawer>
  )
}
