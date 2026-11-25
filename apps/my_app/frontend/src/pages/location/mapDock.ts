/* ============================================================================
 * location/mapDock.ts —— 地图右下角的「图层图例停靠区」
 * ============================================================================
 * 「覆盖 RSRP」和「LTE 小区」两块图例都在右下角。各自绝对定位会互相压住，
 * 所以在 Leaflet 容器里放一个纵向 flex 容器，两块图例都 portal 进来、自动上下排开。
 * 容器本身 pointer-events:none（不挡地图拖动），图例自己再打开 pointer-events。
 * ========================================================================== */
import { useEffect, useState } from 'react'
import type L from 'leaflet'

import { isMapAlive } from '../../components/LeafletMap'

const DOCK_CLASS = 'loc-right-dock'

/** 小区扇区图层在 Leaflet `preclick` 里命中扇面后，把 `CellHit` 挂在原生事件的这个键上；
 *  「覆盖 RSRP」的 click 处理看到它就让路（两层同时打开时点扇面只弹小区）。
 *  LTE 与 NR 两层都命中时，`prio` 高的（画在上面的 NR）留下，click 里只有它开弹窗 ——
 *  Leaflet 先把所有 preclick 监听跑完才跑 click，所以与监听器注册顺序无关。
 *  放在这里而不是 LteCellLayer.tsx：组件文件只导出组件，避免 react-refresh 告警、
 *  也避免两个图层文件互相 import。 */
export const CELL_HIT_KEY = '__locCellHit'
/** 悬浮同理：mousemove 里同步举手（写入本层 prio），rAF 里看到更高 prio 就不出提示 */
export const CELL_HOVER_KEY = '__locCellHover'

export type CellHit = { kind: string; prio: number; i: number }
export type CellMarkedEvent = MouseEvent & { [CELL_HIT_KEY]?: CellHit; [CELL_HOVER_KEY]?: number }

export function useRightDock(map: L.Map): HTMLElement | null {
  const [host, setHost] = useState<HTMLElement | null>(null)
  useEffect(() => {
    if (!isMapAlive(map)) return
    const container = map.getContainer()
    let dock = container.querySelector<HTMLElement>(`:scope > .${DOCK_CLASS}`)
    if (!dock) {
      dock = document.createElement('div')
      dock.className = DOCK_CLASS
      Object.assign(dock.style, {
        position: 'absolute',
        right: '10px',
        bottom: '24px',
        zIndex: '1000',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        gap: '8px',
        maxHeight: 'calc(100% - 120px)',
        pointerEvents: 'none',
      })
      container.appendChild(dock)
    }
    setHost(dock)
  }, [map])
  return host
}
