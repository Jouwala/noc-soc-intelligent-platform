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
 *
 * 流量视图的「可读性」两条（2026-11-25，MW 几百个点对挤在一起时数字叠成一团）：
 *   · 百分比标签：全景下只给峰值 ≥ LABEL_MIN_UTIL 的链路显示；其余在悬停 / 选中时显示，
 *     放大到 LABEL_ALL_SCALE 以上全部显示；
 *   · 低负载弱化：< LOW_LOAD_UTIL 的那一段降低不透明度（色值不变，与 IPRAN 专页
 *     同一套 8 档色），悬停 / 选中时恢复。没有数据的段（null）不弱化，仍按 N/A 处理。
 * ========================================================================== */
import { memo, useState, type ReactNode } from 'react'

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

/** 连线整体线宽系数：2026-11-25 按值班反馈「线太粗」整体减半（含流量视图的带宽加粗量） */
const LINK_WIDTH_SCALE = 0.5

/** 全景下始终显示百分比的门槛（峰值方向 ≥ 70%，即 8 档色阶里的橙 / 红两档） */
const LABEL_MIN_UTIL = 70
/** 放大到这个屏幕比例以上，所有有数据的链路都显示百分比 */
const LABEL_ALL_SCALE = 1.6
/** 低于这个利用率的段降低不透明度（0-40% 是灰 / 紫 / 深蓝 / 浅蓝四档） */
const LOW_LOAD_UTIL = 40
const LOW_LOAD_OPACITY = 0.35

function widthFor(edge: NocEdge, mode: 'logical' | 'traffic'): number {
  const base = mediumWidth(edge.medium)
  if (mode !== 'traffic' || !edge.capacityMbps) return base * LINK_WIDTH_SCALE
  // 带宽越大线越粗：100Mbps ≈ +0，10Gbps ≈ +2
  const bonus = Math.min(2.4, Math.max(0, Math.log10(edge.capacityMbps / 100) * 0.7))
  return (base + bonus) * LINK_WIDTH_SCALE
}

export default memo(function DirectionalLink({
  edge, a, b, c, mode, dimmed, selected, scale, onSelect,
}: DirectionalLinkProps) {
  const [hover, setHover] = useState(false)
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
     TX/RX 两个标签必然叠在一起。低于 0.75 先只画颜色，放大后再出数字；
     即使放大，也只有高负载链路常显，其余悬停 / 选中才显示。 */
  const peak = Math.max(
    edge.txUtilization ?? Number.NEGATIVE_INFINITY,
    edge.rxUtilization ?? Number.NEGATIVE_INFINITY,
  )
  const active = hover || selected
  const showLabels = active
    || (scale >= 0.75 && (peak >= LABEL_MIN_UTIL || scale >= LABEL_ALL_SCALE))
  /** 低负载段弱化；悬停 / 选中时恢复全不透明 */
  const segOpacity = (u: number | null | undefined) => (
    !active && u != null && Number.isFinite(u) && u < LOW_LOAD_UTIL ? LOW_LOAD_OPACITY : 1
  )

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
        <path d={p1} fill="none" stroke={weathermapColor(tx)} strokeOpacity={segOpacity(tx)} strokeWidth={width} strokeLinecap="round" />
        <path d={p2} fill="none" stroke={weathermapColor(rx)} strokeOpacity={segOpacity(rx)} strokeWidth={width} strokeLinecap="round" />
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
      onPointerEnter={() => setHover(true)}
      onPointerLeave={() => setHover(false)}
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
          opacity={0.95 * (traffic ? segOpacity(edge.txUtilization) : 1)}
        />
      )}
    </g>
  )
})
