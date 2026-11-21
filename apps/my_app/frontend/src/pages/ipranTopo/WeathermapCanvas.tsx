/* ============================================================================
 * Weathermap 画布（ipranTopo 页的第二个视图）
 * ============================================================================
 * 和 TopoCanvas 的差别只在“版式语言”，不在数据：
 *   - 节点画成 NMS 风格的设备图标，名字贴在图标旁（先右、再下/上/左，放不下就省）；
 *   - 链路按 Weathermap 语义从 50% 处拆成两段，tx / rx 各自上 8 档色，
 *     百分比标签画在图标之上（否则会被图标盖掉），白底把线咬断；
 *   - 故障语义保留：整条断链红色虚线、部分断链浅色虚线；无流量链路是**细实线**
 *     且不标数 —— 141 条里 91 条没有流量，全画虚线会变成一张“点阵网”。
 *
 * 坐标仍用页面算好的 layout.pos（760×540 逻辑画布），靠 viewBox 缩放铺满容器，
 * 不做第二套布局 —— 否则同一份数据在“拓扑”和“Weathermap”两个视图里位置不同，
 * 用户的位置记忆就失效了。
 * ========================================================================== */
import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type ReactElement } from 'react'

import { useI18n } from '../../i18n'
import { WH, useTheme } from '../../theme'
import type { LayoutResult, Pt, TopoEdge, TopoIndex, TopoNode } from './types'
import { widthOf } from './layouts'
import { splitQuadraticAtMid, trafficLabel, weathermapColor } from './trafficScale'

const W = 760
const H = 540

interface Box { x: number; y: number; w: number; h: number }

interface Props {
  nodes: TopoNode[]
  edges: TopoEdge[]
  idx: TopoIndex
  layout: LayoutResult
  showAllLabels: boolean
  includeAccess: boolean
  positions: Record<string, [number, number]>
  editable: boolean
  onMove: (id: string, x: number, y: number) => void
  onCommit: () => void
  focus: number
  selectedEdge: number
  onFocus: (i: number) => void
  onSelectEdge: (i: number) => void
  onBackground: () => void
}

interface Tip {
  x: number
  y: number
  node?: number
  edge?: number
}

const SIZE: Record<number, number> = { 0: 22, 1: 28, 2: 26, 3: 18 }

function hits(a: Box, b: Box): boolean {
  return Math.abs(a.x - b.x) < (a.w + b.w) / 2 + 1 && Math.abs(a.y - b.y) < (a.h + b.h) / 2 + 1
}

/** 站点重名时补 UPE 号，避免图上出现两个一模一样的标签 */
function labelOf(v: TopoNode, dup: boolean): string {
  const site = (v.site || '').trim()
  if (!site) return v.name.replace(/^.*?_/, '').replace(/_UPE\d+$/i, '').replace(/_/g, ' ')
  if (!dup) return site
  const num = (v.name.match(/(\d+)$/) || [])[1]
  return num ? `${site} ${num}` : site
}

export default function WeathermapCanvas({
  nodes, edges, idx, layout, showAllLabels, includeAccess, positions, editable,
  onMove, onCommit, focus, selectedEdge,
  onFocus, onSelectEdge, onBackground,
}: Props) {
  const { t } = useI18n()
  const { colors } = useTheme()
  const { pos: rawPos } = layout
  /* 默认全量；关掉「含接入层」只看骨干（层外 + 核心 + 汇聚） */
  const vis = (i: number) => includeAccess || (nodes[i] && nodes[i].tier <= 2)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  /* 布局是 760×540 的逻辑画布，节点只占其中一条（分层布局尤其扁）。
     这里把**节点坐标**按横竖两个独立系数铺开：图标不变形、线被拉长，
     整张图才能填满画布；缩到 1 倍以下不做，避免挤成一团。 */
  const [dragPos, setDragPos] = useState<{ id: string; x: number; y: number } | null>(null)
  const dragPosRef = useRef<{ id: string; x: number; y: number } | null>(null)
  const nodeDrag = useRef<{ id: string; dx: number; dy: number; sx: number; sy: number } | null>(null)

  const pos = useMemo(() => {
    const idxs = nodes.map((_, i) => i).filter((i) => vis(i))
    if (!idxs.length) return rawPos
    const xs = idxs.map((i) => rawPos[i].x)
    const ys = idxs.map((i) => rawPos[i].y)
    const minX = Math.min(...xs), maxX = Math.max(...xs)
    const minY = Math.min(...ys), maxY = Math.max(...ys)
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2
    const sx = Math.min(2.6, Math.max(1, 680 / Math.max(1, maxX - minX)))
    const sy = Math.min(2.6, Math.max(1, 450 / Math.max(1, maxY - minY)))
    return rawPos.map((p, i) => {
      if (!vis(i)) return p
      if (dragPos && dragPos.id === nodes[i].id) return { x: dragPos.x, y: dragPos.y }
      const saved = positions[nodes[i].id]
      if (saved) return { x: saved[0], y: saved[1] }
      return { x: (p.x - cx) * sx + W / 2, y: (p.y - cy) * sy + H / 2 }
    })
  }, [rawPos, nodes, includeAccess, positions, dragPos])
  const home = { x: 0, y: 0, w: W, h: H }
  const [vb, setVb] = useState(home)
  const [tip, setTip] = useState<Tip | null>(null)
  const drag = useRef<{ x: number; y: number; vb: typeof vb } | null>(null)
  const movedRef = useRef(false)
  const lastHover = useRef('')

  useEffect(() => { setVb({ x: 0, y: 0, w: W, h: H }) }, [layout, includeAccess])

  /* 滚轮缩放：以光标为锚点。React 的 onWheel 是被动监听，不能 preventDefault，
     所以挂原生监听。 */
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      const fx = (e.clientX - rect.left) / rect.width
      const fy = (e.clientY - rect.top) / rect.height
      setVb((prev) => {
        const k = e.deltaY > 0 ? 1.12 : 1 / 1.12
        const w = Math.min(home.w * 2.6, Math.max(home.w * 0.25, prev.w * k))
        const h = w * (H / W)
        return { x: prev.x + (prev.w - w) * fx, y: prev.y + (prev.h - h) * fy, w, h }
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [home.w])

  const dupSites = useMemo(() => {
    const n: Record<string, number> = {}
    for (const v of nodes) n[v.site] = (n[v.site] || 0) + 1
    return n
  }, [nodes])

  /* 图标占位：链路的百分比标签与节点名都要避开图标 */
  const iconBoxes = useMemo<Box[]>(() => nodes.map((v, i) => {
    if (!vis(i)) return { x: -999, y: -999, w: 0, h: 0 }
    const p = pos[i]
    const s = SIZE[v.tier] ?? 20
    return p ? { x: p.x, y: p.y, w: s + 2, h: s + 2 } : { x: -999, y: -999, w: 0, h: 0 }
  }), [nodes, pos, includeAccess])

  const placed: Box[] = []
  const free = (b: Box) => !iconBoxes.some((r) => hits(r, b)) && !placed.some((r) => hits(r, b))

  function softLabel(x: number, y: number, text: string): string {
    const w = text.length * 3.5 + 4
    const b = { x, y, w, h: 7.6 }
    if (!free(b)) return ''
    placed.push(b)
    return `<rect x="${(x - w / 2).toFixed(1)}" y="${(y - 3.8).toFixed(1)}" width="${w.toFixed(1)}" height="7.6" style="fill:${WH.mapBg}"/>` +
      `<text x="${x.toFixed(1)}" y="${(y + 2.6).toFixed(1)}" text-anchor="middle" font-size="7.4" style="fill:${WH.text}">${text}</text>`
  }

  function findLabel(a: Pt, c: Pt, b: Pt, t0: number, text: string): string {
    for (const d of [0, 0.05, -0.05, 0.1, -0.1, 0.16, -0.16, 0.22, -0.22]) {
      const u = Math.min(0.88, Math.max(0.12, t0 + d))
      const uu = 1 - u
      const p = {
        x: uu * uu * a.x + 2 * u * uu * c.x + u * u * b.x,
        y: uu * uu * a.y + 2 * u * uu * c.y + u * u * b.y,
      }
      const html = softLabel(p.x, p.y, text)
      if (html) return html
    }
    return ''
  }

  /* 先把每条链路的几何算出来，图、标签各画一遍；标签放到图标之后，避免被盖住 */
  const geoms = edges.map((e, i) => {
    if (!vis(idx.byId[e.s]) || !vis(idx.byId[e.t])) return null
    const a = pos[idx.byId[e.s]]
    const b = pos[idx.byId[e.t]]
    if (!a || !b) return null
    const dx = b.x - a.x
    const dy = b.y - a.y
    const len = Math.hypot(dx, dy) || 1
    const bend = Math.min(len * 0.05, 7) * (i % 2 ? 1 : -1)
    const c = { x: (a.x + b.x) / 2 - (dy / len) * bend, y: (a.y + b.y) / 2 + (dx / len) * bend }
    const { first: f, second: s } = splitQuadraticAtMid(a, c, b)
    const failed = e.down > 0 && e.n > 0 && e.down >= e.n
    const partial = e.down > 0 && !failed
    const na = e.txUtil == null && e.rxUtil == null
    const w = Math.max(0.9, widthOf(e.gb)) * (i === selectedEdge ? 1.8 : 1)
    const p1 = `M${f[0].x.toFixed(1)},${f[0].y.toFixed(1)} Q${f[1].x.toFixed(1)},${f[1].y.toFixed(1)} ${f[2].x.toFixed(1)},${f[2].y.toFixed(1)}`
    const p2 = `M${s[0].x.toFixed(1)},${s[0].y.toFixed(1)} Q${s[1].x.toFixed(1)},${s[1].y.toFixed(1)} ${s[2].x.toFixed(1)},${s[2].y.toFixed(1)}`
    const hit = `M${a.x.toFixed(1)},${a.y.toFixed(1)} Q${c.x.toFixed(1)},${c.y.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}`
    return { e, i, a, c, b, p1, p2, hit, w, failed, partial, na }
  })

  const lineEls = geoms.map((g) => {
    if (!g) return ''
    const color = g.failed ? WH.danger : null
    const first = color ?? (g.na ? '#5c6673' : weathermapColor(g.e.txUtil))
    const second = color ?? (g.na ? '#5c6673' : weathermapColor(g.e.rxUtil))
    const dash = g.failed ? '4 2.5' : g.partial ? '7 2.5' : undefined
    const op = g.na ? 0.55 : (g.i === selectedEdge ? 1 : 0.92)
    return `<g opacity="${op}">
      <path d="${g.p1}" fill="none" style="stroke:${first}" stroke-width="${g.w}" ${dash ? `stroke-dasharray="${dash}"` : ''} stroke-linecap="butt"/>
      <path d="${g.p2}" fill="none" style="stroke:${second}" stroke-width="${g.w}" ${dash ? `stroke-dasharray="${dash}"` : ''} stroke-linecap="butt"/>
      <path d="${g.hit}" fill="none" stroke="transparent" stroke-width="7" style="cursor:pointer" data-edge="${g.i}"/>
    </g>`
  })

  /* 百分比标签：只给有数据的链路；短链路只标一个，长链路 25% / 75% 各一个 */
  const linkLabelEls = geoms.map((g) => {
    if (!g) return ''
    if (g.failed) return ''
    const tx = trafficLabel(g.e.txUtil)
    const rx = trafficLabel(g.e.rxUtil)
    if (!tx && !rx) return ''
    const short = Math.hypot(g.b.x - g.a.x, g.b.y - g.a.y) < 70
    if (short) return findLabel(g.a, g.c, g.b, 0.5, tx || rx)
    return (tx ? findLabel(g.a, g.c, g.b, 0.25, tx) : '') + (rx ? findLabel(g.a, g.c, g.b, 0.75, rx) : '')
  })

  const nodeEls = nodes.map((v, i) => {
    if (!vis(i)) return ''
    const p = pos[i]
    if (!p) return ''
    const s = SIZE[v.tier] ?? 20
    const icon = v.tier === 0 ? 'cloud' : v.tier === 2 ? 'switch' : 'router'
    const dim = focus >= 0 && focus !== i
    return `<g data-node="${i}" style="cursor:${editable ? 'move' : 'pointer'}" opacity="${dim ? 0.55 : 1}">
      <use href="#wm-${icon}" x="${(p.x - s / 2).toFixed(1)}" y="${(p.y - s / 2).toFixed(1)}" width="${s}" height="${s}"/>
      ${v.up ? '' : `<circle cx="${(p.x + s / 2 - 2).toFixed(1)}" cy="${(p.y - s / 2 + 2).toFixed(1)}" r="3" style="fill:${WH.danger};stroke:${WH.mapBg}" stroke-width="1"/>`}
      ${i === focus ? `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${(s / 2 + 2).toFixed(1)}" fill="none" stroke="${colors[1]}" stroke-width="1.4"/>` : ''}
    </g>`
  })

  /* 节点名：按「核心 → 汇聚 → 接入」依次找位置，右 / 下 / 上 / 左，都放不下就不标 */
  const nodeLabelEls: ReactElement[] = []
  const order = nodes
    .map((v, i) => ({ v, i }))
    .filter(({ i, v }) => vis(i) && (v.tier <= 2 || showAllLabels || i === focus))
    .sort((a, b) => a.v.tier - b.v.tier || b.v.deg - a.v.deg)
  for (const { v, i } of order) {
    const p = pos[i]
    if (!p) continue
    const s = SIZE[v.tier] ?? 20
    const text = labelOf(v, (dupSites[v.site] || 0) > 1)
    const w = text.length * 3.5 + 3
    const cands = [
      { x: p.x + s / 2 + 4 + w / 2, y: p.y + 2.6, anchor: 'middle' as const },
      { x: p.x, y: p.y + s / 2 + 7, anchor: 'middle' as const },
      { x: p.x, y: p.y - s / 2 - 4, anchor: 'middle' as const },
      { x: p.x - s / 2 - 4 - w / 2, y: p.y + 2.6, anchor: 'middle' as const },
    ]
    for (const c of cands) {
      const b = { x: c.x, y: c.y, w, h: 7.6 }
      if (!free(b)) continue
      placed.push(b)
      nodeLabelEls.push(
        <text key={v.id} x={c.x.toFixed(1)} y={c.y.toFixed(1)} textAnchor={c.anchor} fontSize="7.4"
          style={{ fill: WH.text, stroke: WH.mapBg }} strokeWidth="2.2" paintOrder="stroke"
          strokeLinejoin="round" pointerEvents="none">{text}</text>,
      )
      break
    }
  }

  function svgPoint(ev: { clientX: number; clientY: number }) {
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return null
    return {
      x: vb.x + ((ev.clientX - rect.left) / rect.width) * vb.w,
      y: vb.y + ((ev.clientY - rect.top) / rect.height) * vb.h,
    }
  }

  function onSvgClick(ev: ReactMouseEvent<SVGSVGElement>) {
    const el = ev.target as SVGElement
    if (movedRef.current) { movedRef.current = false; return }
    if (editable) return // 编辑态点击不弹抽屉，避免拖完顺手打开
    const edge = el.getAttribute('data-edge')
    if (edge != null) { onSelectEdge(Number(edge)); return }
    const node = el.parentElement?.getAttribute('data-node')
    if (node != null) { onFocus(Number(node)); return }
    onBackground()
  }

  function onPointerDown(ev: ReactPointerEvent<SVGSVGElement>) {
    const el = ev.target as Element
    if (el.getAttribute('data-edge') != null) return
    const nodeAttr = el.getAttribute('data-node') ?? el.parentElement?.getAttribute('data-node')
    if (nodeAttr != null) {
      if (!editable) return
      const i = Number(nodeAttr)
      const p = pos[i]
      const sp = svgPoint(ev)
      if (!p || !sp) return
      nodeDrag.current = { id: nodes[i].id, dx: p.x - sp.x, dy: p.y - sp.y, sx: ev.clientX, sy: ev.clientY }
      movedRef.current = false
      const next = { id: nodes[i].id, x: p.x, y: p.y }
      dragPosRef.current = next
      setDragPos(next)
      ;(ev.currentTarget as SVGSVGElement).setPointerCapture(ev.pointerId)
      ev.preventDefault()
      return
    }
    drag.current = { x: ev.clientX, y: ev.clientY, vb }
    movedRef.current = false
    ;(ev.currentTarget as SVGSVGElement).setPointerCapture(ev.pointerId)
  }

  function onPointerMove(ev: ReactPointerEvent<SVGSVGElement>) {
    const nd = nodeDrag.current
    if (nd) {
      const sp = svgPoint(ev)
      if (!sp) return
      if (Math.abs(ev.clientX - nd.sx) + Math.abs(ev.clientY - nd.sy) > 3) movedRef.current = true
      const next = {
        id: nd.id,
        x: Math.max(-20, Math.min(W + 20, sp.x + nd.dx)),
        y: Math.max(-20, Math.min(H + 20, sp.y + nd.dy)),
      }
      dragPosRef.current = next
      setDragPos(next)
      return
    }
    const d = drag.current
    if (!d) return
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return
    const dx = (ev.clientX - d.x) * (d.vb.w / rect.width)
    const dy = (ev.clientY - d.y) * (d.vb.h / rect.height)
    if (Math.abs(ev.clientX - d.x) + Math.abs(ev.clientY - d.y) > 3) movedRef.current = true
    setVb({ x: d.vb.x - dx, y: d.vb.y - dy, w: d.vb.w, h: d.vb.h })
  }

  function onPointerUp() {
    if (nodeDrag.current && dragPosRef.current) {
      const d = dragPosRef.current
      onMove(d.id, d.x, d.y)
      onCommit()
      dragPosRef.current = null
      setDragPos(null)
    }
    nodeDrag.current = null
    drag.current = null
  }

  function showTip(ev: ReactMouseEvent, next: Omit<Tip, 'x' | 'y'>) {
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return
    setTip({ ...next, x: ev.clientX - rect.left, y: ev.clientY - rect.top })
  }

  const tierName = (tier: number) => tier === 0 ? t('ipranTopo.tier.0')
    : tier === 1 ? t('ipranTopo.tier.1')
      : tier === 2 ? t('ipranTopo.tier.2')
        : t('ipranTopo.tier.3')

  const tipNode = tip?.node != null ? nodes[tip.node] : null
  const tipEdge = tip?.edge != null ? edges[tip.edge] : null
  const tipEdgeNames = tipEdge
    ? [nodes[idx.byId[tipEdge.s]]?.name, nodes[idx.byId[tipEdge.t]]?.name]
    : null

  return (
    <div ref={wrapRef} style={{ position: 'absolute', inset: 0 }}>
      <svg
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
        preserveAspectRatio="xMidYMid meet"
        style={{ width: '100%', height: '100%', display: 'block', background: WH.mapBg, cursor: editable ? 'default' : 'grab', touchAction: 'none' }}
        onClick={onSvgClick}
        onDoubleClick={() => setVb(home)}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => { onPointerUp(); lastHover.current = ''; setTip(null) }}
        onMouseMove={(ev) => {
          const el = ev.target as SVGElement
          const edge = el.getAttribute('data-edge')
          const node = el.parentElement?.getAttribute('data-node')
          const key = edge != null ? `e${edge}` : node != null ? `n${node}` : ''
          if (key === lastHover.current) return
          lastHover.current = key
          if (edge != null) showTip(ev, { edge: Number(edge) })
          else if (node != null) showTip(ev, { node: Number(node) })
          else setTip(null)
        }}
        role="img"
        aria-label={t('ipranTopo.wm.aria')}
      >
        <defs>
          <symbol id="wm-router" viewBox="0 0 44 32">
            <rect x="7" y="3" width="30" height="5" rx="2.5" fill="#33597d" />
            <rect x="2" y="8" width="40" height="20" rx="3" fill="#517ea8" stroke="#2c4a66" strokeWidth="1.2" />
            <path d="M9 18h9m-4-3.5 4 3.5-4 3.5M35 18h-9m4-3.5-4 3.5 4 3.5" fill="none" stroke="#eaf3fb" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          </symbol>
          <symbol id="wm-switch" viewBox="0 0 48 26">
            <rect x="2" y="2" width="44" height="22" rx="2.5" fill="#5b86ad" stroke="#2c4a66" strokeWidth="1.2" />
            <g fill="#d6e8f7">
              <rect x="7" y="9" width="4" height="4" /><rect x="13" y="9" width="4" height="4" />
              <rect x="19" y="9" width="4" height="4" /><rect x="25" y="9" width="4" height="4" />
              <rect x="31" y="9" width="4" height="4" /><rect x="37" y="9" width="4" height="4" />
              <rect x="7" y="16" width="4" height="3" /><rect x="13" y="16" width="4" height="3" />
              <rect x="19" y="16" width="4" height="3" /><rect x="25" y="16" width="4" height="3" />
              <rect x="31" y="16" width="4" height="3" /><rect x="37" y="16" width="4" height="3" />
            </g>
          </symbol>
          <symbol id="wm-cloud" viewBox="0 0 56 36">
            <path d="M14 31c-6.2 0-10.5-4.1-10.5-9.2 0-4.3 2.9-7.8 7.6-8.6C12.3 7.2 17.2 3.5 23 3.5c6.1 0 11 3.5 12 9.1 5.9.2 10 4 10 9.1 0 5.2-4.6 9.3-10.8 9.3z" fill="#eceff1" stroke="#98a2ad" strokeWidth="1.4" />
          </symbol>
        </defs>
        <rect x="-2000" y="-2000" width={5000} height={5000} style={{ fill: WH.mapBg }} />
        {/* 图形由字符串拼出：SVG 元素多，拼字符串比几百个 React 元素轻；
            点击/悬停统一在 <svg> 上做事件委托 */}
        <g dangerouslySetInnerHTML={{ __html: `${lineEls.join('')}${nodeEls.join('')}${linkLabelEls.join('')}` }} />
        {nodeLabelEls}
      </svg>

      {tip && (tipNode || tipEdge) && (
        <div style={{
          position: 'absolute',
          left: Math.min(tip.x + 12, Math.max(8, (wrapRef.current?.clientWidth ?? 640) - 312)),
          top: Math.min(tip.y + 12, 400),
          maxWidth: 300, padding: '6px 8px',
          background: WH.cardBg, border: `1px solid ${WH.cardBorder}`, borderRadius: 4,
          fontSize: 11, lineHeight: 1.5, color: WH.text, pointerEvents: 'none',
          boxShadow: '2px 2px 0 rgba(0,0,0,.08)', zIndex: 3,
        }}>
          {tipNode && (
            <>
              <div style={{ fontWeight: 600 }}>{tipNode.name}</div>
              <div style={{ color: WH.textMuted }}>
                {tipNode.site || '—'} · {tierName(tipNode.tier)}
              </div>
              <div style={{ color: WH.textMuted }}>
                {tipNode.ip || '—'} · {tipNode.up ? t('ipranTopo.status.up') : t('ipranTopo.status.down')}
              </div>
              <div style={{ color: WH.textMuted }}>
                {t('ipranTopo.col.crit')} {tipNode.crit} · {t('ipranTopo.col.major')} {tipNode.major}
              </div>
            </>
          )}
          {tipEdge && tipEdgeNames && (
            <>
              <div style={{ fontWeight: 600 }}>{tipEdgeNames[0]} ↔ {tipEdgeNames[1]}</div>
              <div style={{ color: WH.textMuted }}>
                {tipEdge.gb != null ? `${tipEdge.gb}G` : '—'} · {t('ipranTopo.col.physLinks')} {tipEdge.n}
              </div>
              <div>
                <span style={{ color: WH.textMuted }}>A→B </span>
                {tipEdge.txUtil == null ? t('ipranTopo.detail.na') : `${tipEdge.txUtil.toFixed(2)}%`}
                <span style={{ color: WH.textMuted }}> · B→A </span>
                {tipEdge.rxUtil == null ? t('ipranTopo.detail.na') : `${tipEdge.rxUtil.toFixed(2)}%`}
              </div>
              {tipEdge.down > 0 && (
                <div style={{ color: WH.danger }}>
                  {t('ipranTopo.col.downLinks')} {tipEdge.down}/{tipEdge.n}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}







