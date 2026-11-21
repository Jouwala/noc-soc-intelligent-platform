/* ============================================================================
 * NOC 拓扑中心 —— 有向链路（SVG）
 * ============================================================================
 * 流量视图的「双向分色」规则（与 IPRAN 专页一致，不另造一套）：
 *   · 曲线在 t=0.5 处精确拆成两段（de Casteljau），不是取直线中点；
 *   · **前半段（源侧）= TX**、**后半段（宿侧）= RX**，各自按 8 档色阶上色，
 *     百分比标签分别压在 t=0.25 / t=0.75 处；
 *   · 两个方向都不做平均 —— 一个方向的利用率不能冒充另一个方向；
 *   · 没有流量数据 → 中性灰虚线 + 不画百分比（不是 0% 的灰白）。
 *
 * 链路形态另外表达三件事：
 *   · 规划链路：虚线；
 *   · 示意连接（OLIB）：点线 + 更细；
 *   · 故障：红色虚线 + 目标端断开的小叉。
 * ========================================================================== */
import { memo, type ReactNode } from 'react'

import { WH } from '../../../theme'
import {
  TRAFFIC_NA_COLOR, quadraticAt, splitQuadraticAtMid, trafficLabel, weathermapColor,
} from '../../ipranTopo/trafficScale'
import { mediumWidth, statusColor } from '../registry'
import type { NocEdge } from '../types'

export interface Pt { x: number; y: number }

export interface DirectionalLinkProps {
  edge: NocEdge
  a: Pt
  b: Pt
  /** 二次贝塞尔控制点（同端点多条链路时由画布做垂直偏移） */
  c: Pt
  mode: 'logical' | 'traffic'
  dimmed: boolean
  selected: boolean
  scale: number
  onSelect: (key: string) => void
}

function dashFor(edge: NocEdge): string | undefined {
  if (edge.status === 'down' || edge.status === 'critical') return '6 4'
  if (edge.planned) return '5 4'
  if (edge.schematic) return '2 4'
  if (!edge.trafficAvailable && edge.source !== 'olib') return '4 4'
  return undefined
}

function widthFor(edge: NocEdge, mode: 'logical' | 'traffic'): number {
  const base = mediumWidth(edge.medium)
  if (mode !== 'traffic' || !edge.capacityMbps) return base
  // 带宽越大线越粗：100Mbps ≈ +0，10Gbps ≈ +2
  return base + Math.min(2.4, Math.max(0, Math.log10(edge.capacityMbps / 100) * 0.7))
}

export default memo(function DirectionalLink({
  edge, a, b, c, mode, dimmed, selected, scale, onSelect,
}: DirectionalLinkProps) {
  const traffic = mode === 'traffic' && edge.trafficAvailable
  const width = widthFor(edge, mode)
  const dim = dimmed ? 0.12 : 1

  const path = `M${a.x.toFixed(2)},${a.y.toFixed(2)} Q${c.x.toFixed(2)},${c.y.toFixed(2)} ${b.x.toFixed(2)},${b.y.toFixed(2)}`
  const angle = Math.atan2(b.y - c.y, b.x - c.x)
  const arrow = 4.5 / Math.max(0.7, Math.min(scale, 2))
  const ax = b.x - Math.cos(angle) * (arrow * 1.4)
  const ay = b.y - Math.sin(angle) * (arrow * 1.4)

  const font = 8.5 / Math.max(0.7, Math.min(scale, 2))
  /* 默认「适配全景」时 scale 约 0.4~0.6，一对两点链路只有几十像素宽，
     TX/RX 两个标签必然叠在一起。低于 0.75 先只画颜色，放大后再出数字。 */
  const showLabels = scale >= 0.75

  const labelStyle = {
    fill: WH.text,
    stroke: WH.cardBg,
    strokeWidth: font / 3.6,
    paintOrder: 'stroke' as const,
    pointerEvents: 'none' as const,
  }

  let body: ReactNode
  if (traffic) {
    const { mid, first, second } = splitQuadraticAtMid(a, c, b)
    const p1 = `M${first[0].x.toFixed(2)},${first[0].y.toFixed(2)} Q${first[1].x.toFixed(2)},${first[1].y.toFixed(2)} ${first[2].x.toFixed(2)},${first[2].y.toFixed(2)}`
    const p2 = `M${second[0].x.toFixed(2)},${second[0].y.toFixed(2)} Q${second[1].x.toFixed(2)},${second[1].y.toFixed(2)} ${second[2].x.toFixed(2)},${second[2].y.toFixed(2)}`
    const tx = edge.txUtilization
    const rx = edge.rxUtilization
    const l1 = quadraticAt(a, c, b, 0.25)
    const l2 = quadraticAt(a, c, b, 0.75)
    body = (
      <>
        <path d={p1} fill="none" stroke={weathermapColor(tx)} strokeWidth={width} strokeLinecap="round" />
        <path d={p2} fill="none" stroke={weathermapColor(rx)} strokeWidth={width} strokeLinecap="round" />
        {showLabels && tx != null && (
          <text x={l1.x} y={l1.y - 3} textAnchor="middle" fontSize={font} {...labelStyle}>
            {`TX ${trafficLabel(tx)}`}
          </text>
        )}
        {showLabels && rx != null && (
          // RX 压在下半段的下方：与 TX 上下分开，密集点对里也不会互相压字
          <text x={l2.x} y={l2.y + font + 1} textAnchor="middle" fontSize={font} {...labelStyle}>
            {`RX ${trafficLabel(rx)}`}
          </text>
        )}
        {/* 中分点的小刻度：告诉用户哪一段是发送、哪一段是接收 */}
        <circle cx={mid.x} cy={mid.y} r={1.4} fill={WH.cardBg} opacity={0.9} />
      </>
    )
  } else {
    const color = mode === 'traffic' ? TRAFFIC_NA_COLOR : statusColor(edge.status)
    body = (
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={width}
        strokeDasharray={dashFor(edge)}
        strokeLinecap="round"
      />
    )
  }

  return (
    <g
      opacity={dim}
      style={{ cursor: 'pointer' }}
      onClick={(e) => { e.stopPropagation(); onSelect(edge.key) }}
      data-interactive="1"
    >
      {/* 透明加粗命中区：细线也能点中 */}
      <path d={path} fill="none" stroke="transparent" strokeWidth={Math.max(9, width + 7)} />
      {selected && (
        <path d={path} fill="none" stroke={WH.teal} strokeWidth={width + 3} opacity={0.4} strokeLinecap="round" />
      )}
      {body}
      {/* 方向箭头：TX 段末端（源→宿） */}
      {!edge.planned && (
        <polygon
          points={`0,0 ${-arrow * 1.8},${-arrow * 0.75} ${-arrow * 1.8},${arrow * 0.75}`}
          transform={`translate(${ax.toFixed(2)},${ay.toFixed(2)}) rotate(${(angle * 180 / Math.PI).toFixed(1)})`}
          fill={traffic ? weathermapColor(edge.txUtilization) : statusColor(edge.status)}
          opacity={0.95}
        />
      )}
    </g>
  )
})
