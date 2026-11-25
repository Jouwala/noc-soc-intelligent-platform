/* ============================================================================
 * NOC 拓扑中心 —— SVG 逻辑画布
 * ============================================================================
 * 一套画布同时服务两个视图：
 *   · logical —— 状态色：链路按 down/critical/major/ok 着色，故障虚线；
 *   · traffic —— Weathermap：链路在中点拆成 TX/RX 两段，各按 8 档色阶；
 *     没有流量的链路画中性灰虚线并**不写百分比**。
 *
 * 交互与 IPRAN 专页保持一致（值班人员不用重新学）：
 *   滚轮缩放（以光标为中心）、拖动平移、双击/「适配」回全景、点设备聚焦、
 *   点链路开详情、点空白取消选中。画布本身不取数，只吃统一模型。
 *
 * 性能：三个来源合起来实测约 1100+ 节点 / 1100+ 边。布局是确定性的
 * O(n+e) 分层环（见 layouts/graphLayout.ts），节点与链路都 memo 化，
 * 缩放/拖动只改一个 transform，不重建子树。
 * ========================================================================== */
import {
  useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent,
} from 'react'

import { WH } from '../../../theme'
import { layoutGraph } from '../layouts/graphLayout'
import { focusSet } from '../model'
import { nodeFillStatus, statusColor } from '../registry'
import type { NocEdge, NocNode } from '../types'
import DeviceNode from './DeviceNode'
import DirectionalLink, { type Pt } from './DirectionalLink'

export interface ViewCmd { seq: number; kind: 'in' | 'out' | 'fit' }

export interface TopologyCanvasProps {
  nodes: NocNode[]
  edges: NocEdge[]
  mode: 'logical' | 'traffic'
  selectedNode: string | null
  selectedEdge: string | null
  onSelectNode: (key: string) => void
  onSelectEdge: (key: string) => void
  onBackground: () => void
  showLabels: boolean
  focus: string | null
  viewCmd: ViewCmd | null
  /** 变更即回全景（切视图/换来源/换层） */
  resetKey: string
  /** 搜索定位：把这个点移到视口中心 */
  locate: { key: string; token: number } | null
}

interface View { k: number; tx: number; ty: number }

const MIN_K = 0.35
const MAX_K = 8

function clamp(v: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, v)) }

export default function TopologyCanvas({
  nodes, edges, mode, selectedNode, selectedEdge,
  onSelectNode, onSelectEdge, onBackground, showLabels, focus, viewCmd, resetKey, locate,
}: TopologyCanvasProps) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  // 流量视图按负载档位排分量：满载的点对排到左上角（见 layouts/graphLayout.ts）
  const layout = useMemo(
    () => layoutGraph(nodes, edges, { sortByLoad: mode === 'traffic' }),
    [nodes, edges, mode],
  )
  const [view, setView] = useState<View>({ k: 1, tx: 0, ty: 0 })
  /** 画布实际像素宽：user-space 的 k 不等于屏幕缩放（viewBox 会被缩到卡片宽度），
   *  标签/箭头是否可读必须按**屏幕比例**判断。 */
  const [pixelWidth, setPixelWidth] = useState(900)

  const W = layout.width
  const H = layout.height

  useEffect(() => {
    const el = svgRef.current?.parentElement
    if (!el) return
    const sync = () => setPixelWidth(el.clientWidth || 900)
    sync()
    const ro = new ResizeObserver(sync)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  /** 一个 user 单位在屏幕上占多少像素 */
  const screenScale = (view.k * pixelWidth) / Math.max(W, 1)

  /* ---------------- 几何 ---------------- */
  const nodeAt = useMemo(() => {
    const m = new Map<string, NocNode>()
    for (const n of nodes) m.set(n.key, n)
    return m
  }, [nodes])

  const geom = useMemo(() => {
    // 同一对端点之间可能有多条链路（OLIB 尤其多）：按无序对分组，给不同垂直偏移
    const pairCount = new Map<string, number>()
    const out: { edge: NocEdge; a: Pt; b: Pt; c: Pt }[] = []
    for (const e of edges) {
      const a = layout.pos[e.sourceNode]
      const b = layout.pos[e.targetNode]
      if (!a || !b) continue
      const pair = e.sourceNode < e.targetNode
        ? `${e.sourceNode}|${e.targetNode}`
        : `${e.targetNode}|${e.sourceNode}`
      const nth = pairCount.get(pair) ?? 0
      pairCount.set(pair, nth + 1)
      const mx = (a.x + b.x) / 2
      const my = (a.y + b.y) / 2
      const dx = b.x - a.x
      const dy = b.y - a.y
      const len = Math.max(1, Math.hypot(dx, dy))
      const bend = nth === 0 ? 0 : (nth % 2 === 1 ? 1 : -1) * Math.ceil(nth / 2) * 18
      const nx = -dy / len
      const ny = dx / len
      out.push({ edge: e, a, b, c: { x: mx + nx * bend, y: my + ny * bend } })
    }
    return out
  }, [edges, layout])

  const labelKeys = useMemo(() => {
    if (showLabels) return null // null = 全部显示
    const set = new Set<string>()
    for (const n of nodes) {
      if (n.degree >= 5 || n.deviceType === 'core' || n.status === 'down') set.add(n.key)
    }
    return set
  }, [nodes, showLabels])

  const focusKeep = useMemo(() => focusSet({ nodes, edges, sources: [] }, focus), [nodes, edges, focus])

  /* ---------------- 视口 ---------------- */
  const fit = useCallback(() => {
    const pts = Object.values(layout.pos)
    if (!pts.length) { setView({ k: 1, tx: 0, ty: 0 }); return }
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const p of pts) {
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x)
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y)
    }
    const bw = Math.max(1, maxX - minX)
    const bh = Math.max(1, maxY - minY)
    const k = clamp(Math.min(W / bw, H / bh) * 0.9, MIN_K, MAX_K)
    setView({ k, tx: W / 2 - ((minX + maxX) / 2) * k, ty: H / 2 - ((minY + maxY) / 2) * k })
  }, [layout, W, H])

  /* 数据/重置键变化 → 回全景。
     注意只认 resetKey：layout 会随每次取数变成新对象，若把 fit 放进依赖，
     自动刷新（静默重取）也会把用户的缩放/平移拽回全景。 */
  const fitRef = useRef(fit)
  fitRef.current = fit
  useEffect(() => { fitRef.current() }, [resetKey])

  useEffect(() => {
    if (!viewCmd) return
    if (viewCmd.kind === 'fit') { fit(); return }
    const f = viewCmd.kind === 'in' ? 1.3 : 1 / 1.3
    setView((v) => {
      const k = clamp(v.k * f, MIN_K, MAX_K)
      const cx = W / 2
      const cy = H / 2
      return { k, tx: cx - ((cx - v.tx) / v.k) * k, ty: cy - ((cy - v.ty) / v.k) * k }
    })
    // 只认 seq，避免 kind 改变但 seq 相同时误触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewCmd?.seq])

  /* 搜索定位：把目标点移到视口中心 */
  useEffect(() => {
    if (!locate) return
    const p = layout.pos[locate.key]
    if (!p) return
    setView((v) => ({ k: Math.max(v.k, 1.6), tx: W / 2 - p.x * Math.max(v.k, 1.6), ty: H / 2 - p.y * Math.max(v.k, 1.6) }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locate?.token])

  const toUser = useCallback((clientX: number, clientY: number): Pt | null => {
    const el = svgRef.current
    if (!el) return null
    const ctm = el.getScreenCTM()
    if (!ctm) return null
    const p = el.createSVGPoint()
    p.x = clientX
    p.y = clientY
    const q = p.matrixTransform(ctm.inverse())
    return { x: q.x, y: q.y }
  }, [])

  /* 原生 wheel：React 的 onWheel 在根节点是 passive，preventDefault 会失效 */
  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const q = toUser(e.clientX, e.clientY)
      if (!q) return
      const factor = Math.exp(-e.deltaY * 0.0015)
      setView((v) => {
        const k = clamp(v.k * factor, MIN_K, MAX_K)
        const px = (q.x - v.tx) / v.k
        const py = (q.y - v.ty) / v.k
        return { k, tx: q.x - px * k, ty: q.y - py * k }
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [toUser])

  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean } | null>(null)
  const [dragging, setDragging] = useState(false)

  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return
    /* 点在设备/链路上时不要开始拖拽：否则 pointerup 会把「点选」当成
       「点空白」再清空一次，抽屉刚打开就被关掉。 */
    const target = e.target as Element | null
    if (target?.closest?.('[data-interactive]')) return
    const q = toUser(e.clientX, e.clientY)
    if (!q) return
    drag.current = { x: q.x, y: q.y, tx: view.tx, ty: view.ty, moved: false }
    setDragging(true)
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current
    if (!d) return
    const q = toUser(e.clientX, e.clientY)
    if (!q) return
    const dx = q.x - d.x
    const dy = q.y - d.y
    if (Math.abs(dx) + Math.abs(dy) > 2) d.moved = true
    setView((v) => ({ ...v, tx: d.tx + dx, ty: d.ty + dy }))
  }

  const onPointerUp = () => {
    const d = drag.current
    drag.current = null
    setDragging(false)
    if (d && !d.moved) onBackground()
  }

  const empty = nodes.length === 0

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      style={{ width: '100%', height: '100%', display: 'block', background: WH.mapBg, cursor: dragging ? 'grabbing' : 'grab' }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={() => { drag.current = null; setDragging(false) }}
      onDoubleClick={fit}
    >
      <g transform={`translate(${view.tx.toFixed(2)},${view.ty.toFixed(2)}) scale(${view.k.toFixed(4)})`}>
        {geom.map(({ edge, a, b, c }) => (
          <DirectionalLink
            key={edge.key}
            edge={edge}
            a={a}
            b={b}
            c={c}
            mode={mode}
            scale={screenScale}
            selected={selectedEdge === edge.key}
            dimmed={focusKeep.size > 0 && !focusKeep.has(edge.sourceNode) && !focusKeep.has(edge.targetNode)}
            onSelect={onSelectEdge}
          />
        ))}
        {nodes.map((n) => {
          const p = layout.pos[n.key]
          if (!p) return null
          const show = labelKeys === null || labelKeys.has(n.key) || selectedNode === n.key
          return (
            <DeviceNode
              key={n.key}
              node={n}
              x={p.x}
              y={p.y}
              // 填充只表达在线/离线；告警走右上角角标（见 registry.nodeFillStatus）
              color={statusColor(nodeFillStatus(n))}
              dimmed={focusKeep.size > 0 && !focusKeep.has(n.key)}
              selected={selectedNode === n.key}
              showLabel={show}
              scale={screenScale}
              onSelect={onSelectNode}
            />
          )
        })}
      </g>
      {empty && (
        <text x={W / 2} y={H / 2} textAnchor="middle" fontSize={14} fill={WH.textDim}>
          —
        </text>
      )}
    </svg>
  )
}
