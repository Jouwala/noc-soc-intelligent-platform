/* ============================================================================
 * OLIB FO 网络拓扑 —— 画布（矢量图 / 骨干示意）
 * ============================================================================
 * 交互与参考页一致：
 *   · 物理 / 逻辑图：滚轮缩放（以光标为中心）、拖动平移、重置适应窗口、全屏；
 *   · 物理图上叠加 667 个站点点位（灰=普通 / 红=5G / 蓝=ISP / 紫=骨干核心），
 *     点位半径按 1/scale 反向补偿，放大后仍保持约 3px，不会糊成一团；
 *   · 选中站点画十字虚线 + 橙色脉冲光圈 + 名称标签；右下角缩略图显示当前视野；
 *   · 骨干示意图与原图共用同一套平移缩放。
 *
 * 2026-11-16：按用户要求去掉「站点分布」视图（散点图），只保留原图两页 + 骨干示意；
 *   ScatterView.tsx 与配套的 SECTOR_COLORS / i18n 键一并删除，不留死代码。
 *
 * 2026-11-20（用户要求「?page=olibTopo 去掉 backbone 这个页签」）：本组件是 v1
 *   （?page=olibTopo）与 v2（?page=olibTopo_v2）共用的画布，两页对视图的需求不同：
 *   · v1 只剩「物理 / 逻辑」两个页签 —— 由调用方通过 `views` 传进来；
 *   · v2 仍保留「骨干核心」模式（它用自己的 Segmented 切换，并隐藏本组件的页签条）。
 *   故这里**不删 core 视图**，而是把「显示哪几个页签」参数化，谁用谁声明。
 *   `views` 缺省仍是三档，v2 不用改。
 *
 * 清晰度：原图是 PDF 矢量导出的 SVG，缩放时改的是**布局宽高**（内容尺寸 × scale），
 * 让浏览器按新尺寸重绘矢量，而不是用 CSS transform 把小图放大 —— 后者在放大后一定糊。
 * 平移只改 transform，不触发重绘。
 * ========================================================================== */
import {
  forwardRef, memo, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
  type PointerEvent as ReactPointerEvent, type MouseEvent as ReactMouseEvent,
} from 'react'
import { Button, Space, message } from 'antd'
import {
  EyeInvisibleOutlined, EyeOutlined, FullscreenExitOutlined, FullscreenOutlined,
  ReloadOutlined, ZoomInOutlined, ZoomOutOutlined,
} from '@ant-design/icons'

import { useI18n } from '../../i18n'
import { WH, useTheme } from '../../theme'
import {
  LOGICAL_PINS, SITES, TOPO_DOCS, TOPO_PAGE_SIZE,
  findCoreNodeBySite, findLogicalPin, findSiteByName, siteColor, sitePercent,
  type TopoSite, type TopoView,
} from './topologyData'
import CoreSchematic from './CoreSchematic'

export interface TopologyViewerHandle {
  fit: () => void
  /** 当前页签内全图适应（选中站点；有点位则高亮） */
  locate: (site: TopoSite) => void
  /** 当前页签内放大到站点（物理 / 逻辑标签 / 骨干节点） */
  zoomTo: (site: TopoSite) => void
  /** 搜索选中：同 zoomTo，始终留在当前页签 */
  focusHere: (site: TopoSite) => void
}

interface Props {
  view: TopoView
  onViewChange: (v: TopoView) => void
  /** 工具条上提供哪几档视图（顺序即按钮顺序）。缺省三档全给（v2 靠 CSS 隐藏本页签条） */
  views?: TopoView[]
  sitesVisible: boolean
  onSitesVisibleChange: (v: boolean) => void
  selected: TopoSite | null
  onSelect: (s: TopoSite | null) => void
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

/** 原图尺寸（缩放坐标系）；loaded 后才画 overlay */
type Natural = { w: number; h: number; view: 'physical' | 'logical' }

/** 站点圆点：只在 scale / 选中项变化时重渲染，拖拽平移不碰这 667 个节点 */
const SiteDots = memo(function SiteDots({
  w, h, scale, selectedIndex, onSelect,
}: {
  w: number
  h: number
  scale: number
  selectedIndex: number
  onSelect: (s: TopoSite) => void
}) {
  const r = (selected: boolean) => ((selected ? 6.2 : 3.2) / Math.max(scale, 0.04)).toFixed(2)
  return (
    <>
      {SITES.map((s, i) => {
        const selected = i === selectedIndex
        return (
          <circle
            key={s.name}
            className={selected ? 'topo-site is-selected' : 'topo-site'}
            cx={(s.x / 1000 * w).toFixed(2)}
            cy={(s.y / 1000 * h).toFixed(2)}
            r={r(selected)}
            fill={siteColor(s)}
            onClick={e => { e.stopPropagation(); onSelect(s) }}
          >
            <title>
              {`${s.name} · ${s.sector}${s.is5g ? ' · 5G' : ''}${s.isp ? ' · ISP' : ''}`}
            </title>
          </circle>
        )
      })}
    </>
  )
})

/** 逻辑图点位：20260901 逻辑图上能匹配到站名的标签（108 个），不是全量 667 */
const LogicalDots = memo(function LogicalDots({
  w, h, scale, selectedName, onSelect,
}: {
  w: number
  h: number
  scale: number
  selectedName: string | null
  onSelect: (s: TopoSite) => void
}) {
  const r = (selected: boolean) => ((selected ? 6.2 : 3.8) / Math.max(scale, 0.04)).toFixed(2)
  return (
    <>
      {LOGICAL_PINS.map(pin => {
        const site = findSiteByName(pin.name)
        if (!site) return null
        const selected = !!selectedName && selectedName === site.name
        return (
          <circle
            key={pin.name}
            className={selected ? 'topo-site is-selected' : 'topo-site'}
            cx={(pin.x / 1000 * w).toFixed(2)}
            cy={(pin.y / 1000 * h).toFixed(2)}
            r={r(selected)}
            fill={siteColor(site)}
            onClick={e => { e.stopPropagation(); onSelect(site) }}
          >
            <title>{`${site.name} · ${site.sector}`}</title>
          </circle>
        )
      })}
    </>
  )
})

const VIEW_OPTIONS: { value: TopoView; key: string }[] = [
  { value: 'physical', key: 'olibTopo.view.physical' },
  { value: 'logical', key: 'olibTopo.view.logical' },
  { value: 'core', key: 'olibTopo.view.core' },
]

const ALL_VIEWS: TopoView[] = VIEW_OPTIONS.map(o => o.value)

const CSS = `
.topo-toolbar{display:flex;flex-wrap:wrap;gap:var(--wh-space-sm);align-items:center;
  padding:var(--wh-space-sm) var(--wh-space-md);border-bottom:1px solid var(--wh-card-border)}
/* 撑满模式（2026-11-16）：页面把画布卡片做成 flex 列并占满可用高度，
   画布本体吃掉「工具条 + 页底说明」之外的剩余高度，页底不再留一大块空白。
   只作用于 .topo-fill 内，maponly / 全屏 / 其他页面的 62vh 默认值不受影响。 */
.topo-fill{display:flex;flex-direction:column;flex:1 1 auto;height:100%;min-height:0;overflow:hidden}
.topo-fill>.topo-toolbar,.topo-fill>.topo-hint{flex:0 0 auto}
.topo-fill>.topo-viewer{flex:1 1 auto;height:auto;min-height:20rem}
.topo-views{display:flex;flex-wrap:wrap;gap:6px;min-width:0}
.topo-viewer{position:relative;height:62vh;min-height:420px;overflow:hidden;cursor:grab;touch-action:none;
  user-select:none}
.topo-viewer:active{cursor:grabbing}
.topo-viewer:fullscreen{height:100vh;min-height:100vh;background:#0b0f19}
.topo-stage{position:absolute;left:0;top:0;transform-origin:0 0;will-change:transform}
.topo-stage img{display:block;max-width:none;user-select:none;-webkit-user-drag:none;
  box-shadow:0 8px 30px rgba(0,0,0,.22)}
.topo-overlay{position:absolute;left:0;top:0;overflow:visible;pointer-events:none}
.topo-site{pointer-events:all;cursor:pointer;stroke:#fff;stroke-width:1.6px;
  vector-effect:non-scaling-stroke;opacity:.9;transition:opacity .12s}
.topo-site:hover{opacity:1;stroke:#111;stroke-width:2.4px}
.topo-site.is-selected{opacity:1;stroke:#ff7900;stroke-width:3px}
.topo-hl{position:absolute;inset:0;pointer-events:none;z-index:5}
.topo-hl .vline{position:absolute;top:0;bottom:0;width:0;border-left:2px dashed rgba(255,90,0,.75)}
.topo-hl .hline{position:absolute;left:0;right:0;height:0;border-top:2px dashed rgba(255,90,0,.75)}
.topo-hl .ring{position:absolute;width:44px;height:44px;margin:-22px 0 0 -22px;border:3px solid #ff5a00;
  border-radius:50%;background:rgba(255,121,0,.18);animation:topo-pulse 1.2s infinite}
.topo-hl .dot{position:absolute;width:10px;height:10px;margin:-5px 0 0 -5px;background:#ff5a00;
  border:2px solid #fff;border-radius:50%}
@keyframes topo-pulse{0%{box-shadow:0 0 0 0 rgba(255,90,0,.45)}100%{box-shadow:0 0 0 20px rgba(255,90,0,0)}}
.topo-tag{position:absolute;z-index:6;max-width:220px;background:#111;color:#fff;font-size:13px;
  font-weight:600;padding:7px 28px 7px 10px;border-radius:8px;white-space:nowrap;box-shadow:0 4px 14px rgba(0,0,0,.3)}
.topo-tag small{display:block;font-weight:400;color:#ffb37e;font-size:11px;margin-top:1px}
.topo-tag button{position:absolute;right:6px;top:6px;border:0;background:transparent;color:#ffb37e;
  cursor:pointer;font-weight:700;font-size:13px;line-height:1;padding:2px}
.topo-minimap{position:absolute;right:10px;bottom:10px;width:180px;z-index:6;overflow:hidden;
  cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.18)}
.topo-minimap .mm-head{font-size:11px;padding:4px 8px;border-bottom:1px solid var(--wh-card-border)}
.topo-minimap .mm-body{position:relative;line-height:0}
.topo-minimap img{width:100%;display:block}
.topo-minimap .mm-dot{position:absolute;width:9px;height:9px;margin:-4px 0 0 -4px;background:#ff5a00;
  border:2px solid #fff;border-radius:50%;pointer-events:none}
.topo-minimap .mm-view{position:absolute;border:2px solid #ff5a00;background:rgba(255,121,0,.15);
  pointer-events:none}
.topo-hint{padding:8px 12px;font-size:12px;line-height:1.6;border-top:1px solid var(--wh-card-border)}
@media (max-width:600px){.topo-viewer{height:52vh}.topo-minimap{width:132px}}
`

const TopologyViewer = forwardRef<TopologyViewerHandle, Props>(function TopologyViewer({
  view, onViewChange, views = ALL_VIEWS, sitesVisible, onSitesVisibleChange, selected, onSelect,
}, ref) {
  const { t } = useI18n()
  const { isDark } = useTheme()
  const viewerRef = useRef<HTMLDivElement>(null)

  const [viewSize, setViewSize] = useState({ w: 0, h: 0 })
  const [transform, setTransform] = useState({ s: 1, x: 0, y: 0 })
  const [natural, setNatural] = useState<Natural | null>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  /** abs：骨干示意用 viewBox 绝对坐标；缺省则用站点千分比换算到当前 content */
  const [pending, setPending] = useState<{
    site: TopoSite
    mode: 'fit' | 'zoom'
    expectView: TopoView
    abs?: { x: number; y: number }
  } | null>(null)

  /* 画布内容尺寸：原图用 natural size，骨干示意用固定 viewBox
   * （2026-11-16 去掉站点分布视图后，非原图的只剩 core 一种） */
  const content = useMemo(() => {
    if (view === 'physical' || view === 'logical') {
      return natural && natural.view === view
        ? { w: natural.w, h: natural.h }
        : TOPO_PAGE_SIZE[view]
    }
    return { w: 1040, h: 540 }
  }, [view, natural])

  const fit = useCallback(() => {
    if (!content || !viewSize.w || !viewSize.h) return
    const s = Math.min(viewSize.w / content.w, viewSize.h / content.h) * 0.98
    setTransform({ s, x: (viewSize.w - content.w * s) / 2, y: (viewSize.h - content.h * s) / 2 })
  }, [content, viewSize])

  /* 内容或可视区域变化时适应窗口（用户缩放后 fit 不变，不会被重置） */
  useEffect(() => { fit() }, [fit])

  useEffect(() => {
    const el = viewerRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setViewSize({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    setViewSize({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [])

  const zoomAt = useCallback((factor: number, cx: number, cy: number) => {
    setTransform(p => {
      // 上限 2.5×（相对 PDF pt，正文约 22px 高，足够阅读）：再大对读图没有帮助，
      // 也会让布局尺寸逼近浏览器画布上限；下限 0.02 便于看全图。
      const ns = clamp(p.s * factor, 0.02, 2.5)
      const f = ns / p.s
      return { s: ns, x: cx - (cx - p.x) * f, y: cy - (cy - p.y) * f }
    })
  }, [])

  /* 滚轮必须用原生监听才能 preventDefault（React 的 wheel 监听是 passive） */
  useEffect(() => {
    const el = viewerRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      zoomAt(e.deltaY < 0 ? 1.12 : 0.89, e.clientX - rect.left, e.clientY - rect.top)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [zoomAt])

  useEffect(() => {
    const onFs = () => setIsFullscreen(document.fullscreenElement === viewerRef.current)
    document.addEventListener('fullscreenchange', onFs)
    return () => document.removeEventListener('fullscreenchange', onFs)
  }, [])

  const zoomToPoint = useCallback((cx: number, cy: number) => {
    if (!content || !viewSize.w) return
    const s = Math.min(viewSize.w / content.w, viewSize.h / content.h) * 6
    setTransform({ s, x: viewSize.w / 2 - cx * s, y: viewSize.h / 2 - cy * s })
  }, [content, viewSize])

  const zoomToSite = useCallback((site: TopoSite) => {
    if (!content) return
    zoomToPoint(site.x / 1000 * content.w, site.y / 1000 * content.h)
  }, [content, zoomToPoint])

  /* 等目标页签 content 就绪再缩放，避免切页/首屏时用空尺寸计算 */
  useEffect(() => {
    if (!pending || view !== pending.expectView || !content) return
    if (pending.mode === 'zoom') {
      if (pending.abs) zoomToPoint(pending.abs.x, pending.abs.y)
      else zoomToSite(pending.site)
    } else fit()
    setPending(null)
  }, [pending, view, content, fit, zoomToSite, zoomToPoint])

  /* 一律留在当前页签定位，不再强制切到物理图 */
  const focusOnView = useCallback((site: TopoSite, mode: 'fit' | 'zoom') => {
    onSelect(site)
    if (view === 'physical') {
      setPending({ site, mode, expectView: 'physical' })
      return
    }
    if (view === 'logical') {
      const pin = findLogicalPin(site)
      if (!pin) {
        message.info(t('olibTopo.search.logicalNoPos', { name: site.name }))
        return
      }
      if (mode === 'fit') {
        setPending({ site, mode: 'fit', expectView: 'logical' })
        return
      }
      const page = content && content.w ? content : TOPO_PAGE_SIZE.logical
      setPending({
        site,
        mode: 'zoom',
        expectView: 'logical',
        abs: { x: pin.x / 1000 * page.w, y: pin.y / 1000 * page.h },
      })
      return
    }
    if (view === 'core') {
      const node = findCoreNodeBySite(site)
      if (!node) {
        message.info(t('olibTopo.search.coreMiss', { name: site.name }))
        return
      }
      if (mode === 'fit') {
        setPending({ site, mode: 'fit', expectView: 'core' })
        return
      }
      setPending({ site, mode: 'zoom', expectView: 'core', abs: { x: node.x, y: node.y } })
    }
  }, [content, onSelect, t, view])

  useImperativeHandle(ref, () => ({
    fit,
    locate: (site: TopoSite) => focusOnView(site, 'fit'),
    zoomTo: (site: TopoSite) => focusOnView(site, 'zoom'),
    focusHere: (site: TopoSite) => focusOnView(site, 'zoom'),
  }), [fit, focusOnView])

  const toggleFullscreen = () => {
    const el = viewerRef.current
    if (!el) return
    if (document.fullscreenElement) void document.exitFullscreen()
    else void el.requestFullscreen()
  }

  /* ---- 拖拽平移 ---- */
  const dragRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null)
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if ((e.target as Element).closest('.topo-site')) return
    dragRef.current = { x: e.clientX, y: e.clientY, tx: transform.x, ty: transform.y }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = dragRef.current
    if (!d) return
    setTransform(p => ({ ...p, x: d.tx + (e.clientX - d.x), y: d.ty + (e.clientY - d.y) }))
  }
  const endDrag = (e: ReactPointerEvent<HTMLDivElement>) => {
    dragRef.current = null
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }

  const selectedIndex = selected ? SITES.indexOf(selected) : -1

  /* 矢量图按「布局尺寸 = 原图尺寸 × scale」渲染，而不是用 CSS transform 缩放：
   * transform 缩放会让浏览器先按小尺寸栅格化再放大，放大后糊；改布局尺寸会按新尺寸
   * 重新栅格化 SVG，任意倍率都清晰。平移仍用 transform，缩放时才改宽高。 */
  const display = content ? { w: content.w * transform.s, h: content.h * transform.s } : null

  /* 高亮位置（视图坐标系）：物理用全量坐标；逻辑用 LOGICAL_PINS 标签坐标 */
  const highlight = useMemo(() => {
    if (!selected || !content) return null
    let x = selected.x
    let y = selected.y
    if (view === 'logical') {
      const pin = findLogicalPin(selected)
      if (!pin) return null
      x = pin.x
      y = pin.y
    } else if (view !== 'physical') {
      return null
    }
    const px = transform.x + (x / 1000) * content.w * transform.s
    const py = transform.y + (y / 1000) * content.h * transform.s
    return { px, py }
  }, [view, selected, content, transform])

  /* 缩略图视野框：由当前 transform 反推可见区域占原图的比例 */
  const minimapRect = useMemo(() => {
    if (view !== 'physical' || !content || !viewSize.w) return null
    const s = transform.s
    const left = Math.max(0, -transform.x / s) / content.w * 100
    const top = Math.max(0, -transform.y / s) / content.h * 100
    const width = Math.min(100, viewSize.w / s / content.w * 100)
    const height = Math.min(100, viewSize.h / s / content.h * 100)
    return { left: `${left}%`, top: `${top}%`, width: `${width}%`, height: `${height}%` }
  }, [view, content, viewSize, transform])

  const onMinimapClick = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (view !== 'physical' || !content) return
    const rect = e.currentTarget.getBoundingClientRect()
    const px = (e.clientX - rect.left) / rect.width
    const py = (e.clientY - rect.top) / rect.height
    setTransform(p => ({
      ...p,
      x: viewSize.w / 2 - px * content.w * p.s,
      y: viewSize.h / 2 - py * content.h * p.s,
    }))
  }

  const hintKey = view === 'core'
    ? 'olibTopo.hint.core'
    : view === 'logical'
      ? 'olibTopo.hint.logical'
      : 'olibTopo.hint.physical'

  return (
    <>
      <style>{CSS}</style>
      <div className="topo-toolbar">
        <div className="topo-views" role="tablist" aria-label={t('olibTopo.view.tabs')}>
          {VIEW_OPTIONS.filter(o => views.includes(o.value)).map(o => (
            <Button
              key={o.value}
              size="small"
              role="tab"
              aria-selected={view === o.value}
              type={view === o.value ? 'primary' : 'default'}
              onClick={() => onViewChange(o.value)}
            >
              {t(o.key)}
            </Button>
          ))}
        </div>
        <Space size={4} wrap style={{ marginLeft: 'auto' }}>
          <Button
            size="small" icon={<ZoomInOutlined />} title={t('olibTopo.zoomIn')} aria-label={t('olibTopo.zoomIn')}
            onClick={() => zoomAt(1.25, viewSize.w / 2, viewSize.h / 2)}
          />
          <Button
            size="small" icon={<ZoomOutOutlined />} title={t('olibTopo.zoomOut')} aria-label={t('olibTopo.zoomOut')}
            onClick={() => zoomAt(0.8, viewSize.w / 2, viewSize.h / 2)}
          />
          <Button size="small" icon={<ReloadOutlined />} onClick={fit}>{t('olibTopo.reset')}</Button>
          <Button
            size="small"
            icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
            onClick={toggleFullscreen}
          >
            {isFullscreen ? t('olibTopo.fullscreenExit') : t('olibTopo.fullscreen')}
          </Button>
          <Button
            size="small"
            type={sitesVisible ? 'default' : 'dashed'}
            icon={sitesVisible ? <EyeOutlined /> : <EyeInvisibleOutlined />}
            onClick={() => onSitesVisibleChange(!sitesVisible)}
            title={sitesVisible ? t('olibTopo.sites.hide') : t('olibTopo.sites.show')}
          >
            {sitesVisible ? t('olibTopo.sites.show') : t('olibTopo.sites.hide')}
          </Button>
        </Space>
      </div>

      <div
        ref={viewerRef}
        className="topo-viewer"
        style={{ background: isDark ? '#0b0f19' : '#eef2f7' }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <div className="topo-stage" style={{ transform: `translate(${transform.x}px,${transform.y}px)` }}>
          {(view === 'physical' || view === 'logical') && (
            <>
              <img
                src={TOPO_DOCS[view]}
                width={display ? display.w : undefined}
                height={display ? display.h : undefined}
                alt={t(view === 'physical' ? 'olibTopo.img.physical' : 'olibTopo.img.logical')}
                draggable={false}
                onLoad={e => setNatural({
                  w: e.currentTarget.naturalWidth,
                  h: e.currentTarget.naturalHeight,
                  view,
                })}
              />
              {view === 'physical' && sitesVisible && natural && natural.view === 'physical' && display && (
                <svg
                  className="topo-overlay"
                  width={display.w}
                  height={display.h}
                  viewBox={`0 0 ${natural.w} ${natural.h}`}
                  aria-label={t('olibTopo.overlay.aria', { n: SITES.length })}
                >
                  <SiteDots
                    w={natural.w}
                    h={natural.h}
                    scale={transform.s}
                    selectedIndex={selectedIndex}
                    onSelect={onSelect}
                  />
                </svg>
              )}
              {view === 'logical' && sitesVisible && natural && natural.view === 'logical' && display && (
                <svg
                  className="topo-overlay"
                  width={display.w}
                  height={display.h}
                  viewBox={`0 0 ${natural.w} ${natural.h}`}
                  aria-label={t('olibTopo.overlay.ariaLogical', { n: LOGICAL_PINS.length })}
                >
                  <LogicalDots
                    w={natural.w}
                    h={natural.h}
                    scale={transform.s}
                    selectedName={selected?.name ?? null}
                    onSelect={onSelect}
                  />
                </svg>
              )}
            </>
          )}
          {view === 'core' && (
            <CoreSchematic
              onSelect={onSelect}
              selected={selected}
              width={1040 * transform.s}
              height={540 * transform.s}
            />
          )}
        </div>

        {highlight && selected && (
          <div className="topo-hl" aria-hidden="true">
            <span className="vline" style={{ left: highlight.px }} />
            <span className="hline" style={{ top: highlight.py }} />
            <span className="ring" style={{ left: highlight.px, top: highlight.py }} />
            <span className="dot" style={{ left: highlight.px, top: highlight.py }} />
          </div>
        )}

        {highlight && selected && (
          <div
            className="topo-tag"
            style={{
              left: Math.min(Math.max(6, highlight.px + 18), Math.max(6, viewSize.w - 220 - 8)),
              top: Math.min(Math.max(6, highlight.py - 52), Math.max(6, viewSize.h - 56 - 8)),
            }}
            onPointerDown={e => e.stopPropagation()}
          >
            {selected.name}
            <button type="button" aria-label={t('olibTopo.detail.close')} onClick={() => onSelect(null)}>
              ✕
            </button>
            <small>{selected.sector}</small>
          </div>
        )}

        {view === 'physical' && (
          <div
            className="topo-minimap"
            style={{ background: WH.cardBg, border: WH.cardBorder, color: WH.textMuted, borderRadius: 'var(--wh-radius-md)' }}
            onPointerDown={e => e.stopPropagation()}
          >
            <div className="mm-head">{t('olibTopo.minimap.title')}</div>
            <div className="mm-body" onClick={onMinimapClick}>
              <img src={TOPO_DOCS.physical} alt="" draggable={false} />
              {selected && (
                <span
                  className="mm-dot"
                  style={{ left: `${sitePercent(selected.x)}%`, top: `${sitePercent(selected.y)}%` }}
                />
              )}
              {minimapRect && <span className="mm-view" style={minimapRect} />}
            </div>
          </div>
        )}
      </div>

      <div className="topo-hint" style={{ color: WH.textMuted }}>
        {t(hintKey, { n: view === 'logical' ? LOGICAL_PINS.length : SITES.length })}
      </div>
    </>
  )
})

export default TopologyViewer
