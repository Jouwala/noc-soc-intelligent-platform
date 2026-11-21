/* ============================================================================
 * OLIB FO 网络拓扑 —— 骨干示意（按 202503 版逻辑图梳理）
 * ============================================================================
 * 节点/链路数据在 topologyData.ts（CORE_NODES / CORE_LINKS）。
 * 点节点时按站名回查第 1 页名单：找得到就选中该站点，找不到给出明确提示，
 * 不假装它是一份可直接下钻的资产表。
 * ========================================================================== */
import { message } from 'antd'
import { useI18n } from '../../i18n'
import {
  CORE_LINKS, CORE_NODES, LINK_COLORS, findCoreNodeBySite, findSiteByName,
  normalizeName, type CoreLinkType, type TopoSite,
} from './topologyData'

const W = 1040
const H = 540

const LINK_ORDER: CoreLinkType[] = ['exist', 'rail', 'p25a', 'dwdm']
const LINK_LEGEND_X = [60, 200, 370, 540]

export default function CoreSchematic({
  onSelect, selected = null, width = W, height = H,
}: {
  onSelect: (s: TopoSite) => void
  /** 当前选中站点：高亮对应骨干节点（搜索定位 / 点击节点共用） */
  selected?: TopoSite | null
  /** 显示尺寸（viewer 按缩放倍率传入，矢量重绘保持清晰） */
  width?: number
  height?: number
}) {
  const { t } = useI18n()
  const byId = new Map(CORE_NODES.map(n => [n.id, n]))
  const selectedNodeId = selected ? findCoreNodeBySite(selected)?.id : undefined
  const selectedNorm = selected ? normalizeName(selected.name) : ''

  const pick = (label: string, siteName?: string) => {
    const site = findSiteByName(siteName)
    if (site) onSelect(site)
    else message.info(t('olibTopo.core.notInList', { name: label }))
  }

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${W} ${H}`}
      style={{ background: '#fff', display: 'block' }}
      role="img"
      aria-label={t('olibTopo.view.core')}
    >
      {CORE_LINKS.map(([a, b, type], i) => {
        const A = byId.get(a)
        const B = byId.get(b)
        if (!A || !B) return null
        return (
          <line
            key={`${a}-${b}-${i}`}
            x1={A.x} y1={A.y} x2={B.x} y2={B.y}
            stroke={LINK_COLORS[type]}
            strokeWidth={type === 'exist' ? 5 : 3}
            strokeDasharray={type === 'dwdm' ? '7 4' : undefined}
            opacity={0.85}
          />
        )
      })}

      {CORE_NODES.map(n => {
        const active = selectedNodeId
          ? n.id === selectedNodeId
          : !!(n.site && selectedNorm && normalizeName(n.site) === selectedNorm)
        return (
          <g
            key={n.id}
            role="button"
            tabIndex={0}
            style={{ cursor: 'pointer' }}
            onClick={e => { e.stopPropagation(); pick(n.label, n.site) }}
            onKeyDown={e => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(n.label, n.site) }
            }}
          >
            <title>{n.label}</title>
            {active && (
              <circle
                cx={n.x} cy={n.y} r={28}
                fill="rgba(255,121,0,.18)" stroke="#ff5a00" strokeWidth={3}
              />
            )}
            <circle cx={n.x} cy={n.y} r={22} fill="transparent" />
            <circle
              cx={n.x} cy={n.y} r={17}
              fill={active ? '#ff5a00' : '#29a7e1'}
              stroke="#fff" strokeWidth={2}
            />
            <text x={n.x} y={n.y + 5} textAnchor="middle" fontSize={13} fill="#fff" fontWeight={700}>✕</text>
            <text
              x={n.x} y={n.y + 32} textAnchor="middle" fontSize={12}
              fill={active ? '#ff5a00' : '#111'} fontWeight={active ? 700 : 400}
            >{n.label}</text>
          </g>
        )
      })}

      <g fontSize={12}>
        {LINK_ORDER.map((type, i) => (
          <g key={type}>
            <rect
              x={LINK_LEGEND_X[i]} y={505} width={16} height={5}
              fill={LINK_COLORS[type]}
            />
            <text x={LINK_LEGEND_X[i] + 22} y={512} fill="#111">
              {t(`olibTopo.legend.link.${type}`)}
            </text>
          </g>
        ))}
        <text x={60} y={528} fill="#555" fontSize={11}>
          {t('olibTopo.legend.footnote')}
        </text>
      </g>
    </svg>
  )
}
