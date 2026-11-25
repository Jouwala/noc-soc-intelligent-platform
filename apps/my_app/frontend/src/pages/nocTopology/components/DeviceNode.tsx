/* ============================================================================
 * NOC 拓扑中心 —— 设备节点（SVG）
 * ============================================================================
 * 视觉规则（与需求「设备图标统一」对应）：
 *   · 形状表示**设备类别**：核心=六边形、汇聚=圆角方、交换机=菱形、
 *     接入=圆点、防火墙=方盾、运营商/云=云、微波=三角、站点=小圆；
 *   · 填充表示**状态**（ok/minor/major/critical/down/unknown/static）；
 *     IPRAN 只按在线/离线填色（registry.nodeFillStatus），告警不进填充；
 *   · 外环表示**来源**（IPRAN 蓝 / MW 黄 / OLIB 青），一眼分清是谁的设备。
 *
 * 告警不做主着色，只在右上角点一颗角标：紧急=红、重要=橙（无紧急时）——
 * IPRAN 实测 80% 网元带紧急告警，
 * 按告警填色会全红、失去区分度（见 ipranTopo 页头注释与数据契约 §3.1）。
 * ========================================================================== */
import { memo } from 'react'

import { WH } from '../../../theme'
import { DEVICE_RADIUS, sourceColor } from '../registry'
import type { NocNode } from '../types'

export interface DeviceNodeProps {
  node: NocNode
  x: number
  y: number
  /** 状态色（由调用方按视图决定：总览=来源色，逻辑=状态色，流量=状态色） */
  color: string
  dimmed: boolean
  selected: boolean
  showLabel: boolean
  /** 画布缩放比：缩放后标签保持可读，不跟着无限放大 */
  scale: number
  onSelect: (key: string) => void
}

function glyph(node: NocNode, r: number, color: string) {
  const common = { fill: color, stroke: WH.cardBg, strokeWidth: 1.2 }
  switch (node.deviceType) {
    case 'core': {
      const pts = Array.from({ length: 6 }, (_, i) => {
        const a = (Math.PI / 3) * i - Math.PI / 2
        return `${(Math.cos(a) * r).toFixed(2)},${(Math.sin(a) * r).toFixed(2)}`
      }).join(' ')
      return <polygon points={pts} {...common} />
    }
    case 'aggregation':
      return <rect x={-r} y={-r} width={r * 2} height={r * 2} rx={r * 0.35} {...common} />
    case 'switch':
      return <polygon points={`0,${-r} ${r},0 0,${r} ${-r},0`} {...common} />
    case 'firewall':
      return (
        <g>
          <path d={`M0 ${-r} L${r} ${-r * 0.45} L${r} ${r * 0.35} Q0 ${r * 1.25} ${-r} ${r * 0.35} L${-r} ${-r * 0.45} Z`} {...common} />
          <line x1={0} y1={-r * 0.6} x2={0} y2={r * 0.55} stroke={WH.cardBg} strokeWidth={1} />
        </g>
      )
    case 'isp':
      return (
        <path
          d={`M${-r} ${r * 0.25} q0 ${-r * 0.55} ${r * 0.5} ${-r * 0.5}
              q${r * 0.15} ${-r * 0.7} ${r * 0.85} ${-r * 0.45}
              q${r * 0.55} ${-r * 0.15} ${r * 0.6} ${r * 0.45}
              q${r * 0.55} ${r * 0.05} ${r * 0.05} ${r * 0.55} Z`}
          {...common}
        />
      )
    case 'microwave':
      return (
        <g>
          <path d={`M0 ${-r} L${r * 0.95} ${r * 0.75} L${-r * 0.95} ${r * 0.75} Z`} {...common} />
          <line x1={0} y1={-r * 0.2} x2={0} y2={r * 0.5} stroke={WH.cardBg} strokeWidth={1} />
        </g>
      )
    case 'access':
      return <circle r={r} {...common} />
    default:
      return <circle r={r * 0.85} {...common} />
  }
}

function DeviceNodeImpl({
  node, x, y, color, dimmed, selected, showLabel, scale, onSelect,
}: DeviceNodeProps) {
  const r = DEVICE_RADIUS[node.deviceType]
  const ring = sourceColor(node.source)
  const label = node.name.length > 20 ? `${node.name.slice(0, 19)}…` : node.name
  // 缩放越大标签越小（相对画布），保证放大时不会糊满屏
  const font = 9 / Math.max(0.65, Math.min(scale, 2.2))

  return (
    <g
      transform={`translate(${x.toFixed(2)},${y.toFixed(2)})`}
      opacity={dimmed ? 0.16 : 1}
      style={{ cursor: 'pointer' }}
      onClick={(e) => { e.stopPropagation(); onSelect(node.key) }}
      role="button"
      aria-label={node.name}
      data-interactive="1"
    >
      {selected && (
        <circle r={r + 6} fill="none" stroke={WH.teal} strokeWidth={1.6} strokeDasharray="3 3" />
      )}
      {/* 来源外环：比设备本体大 2px，深色底上也能看清来源 */}
      <circle r={r + 2} fill="none" stroke={ring} strokeWidth={1.4} opacity={0.9} />
      {glyph(node, r, color)}
      {node.alarms && (node.alarms.crit > 0 || node.alarms.major > 0) && (
        <g transform={`translate(${r + 2},${-r - 2})`}>
          <circle
            r={3.1}
            fill={node.alarms.crit > 0 ? WH.sev.Critical : WH.sev.Major}
            stroke={WH.cardBg}
            strokeWidth={0.8}
          />
        </g>
      )}
      {showLabel && (
        <text
          y={r + 2 + font + 1}
          textAnchor="middle"
          fontSize={font}
          fill={WH.textMuted}
          style={{ pointerEvents: 'none', paintOrder: 'stroke' }}
          stroke={WH.cardBg}
          strokeWidth={font / 4}
        >
          {label}
        </text>
      )}
    </g>
  )
}

export default memo(DeviceNodeImpl)
