/* ============================================================================
 * NOC 拓扑中心 —— 链路历史趋势（纯 SVG）
 * ============================================================================
 * 只画**上游真的给了历史**的链路：
 *   · MW：`edge.history`（日粒度全部日期 / 实时最近 96 个 15 分钟槽）；
 *   · IPRAN：`topo_traffic.py` 只有最新快照，没有历史接口 → 不传 history，
 *     本组件显示「无历史接口」，**不用前端缓存拼假曲线**；
 *   · OLIB：无。
 *
 * 用纯 SVG 而不是 ECharts：一条链路最多 96 个点，没必要为此加载图表库，
 * 也避免 canvas 解析 CSS 变量的问题（线色直接用 WH 令牌）。
 * ========================================================================== */
import { WH } from '../../../theme'
import { useI18n } from '../../../i18n'
import type { NocHistoryPoint } from '../types'

const W = 320
const H = 120
const PAD_L = 28
const PAD_R = 8
const PAD_T = 8
const PAD_B = 18

export default function TrafficTrendPanel({ history }: { history?: NocHistoryPoint[] }) {
  const { t } = useI18n()

  if (!history || history.length < 2) {
    return (
      <div style={{ fontSize: 12, color: WH.textDim, padding: '8px 0' }}>
        {t('nocTopo.detail.noHistory')}
      </div>
    )
  }

  const innerW = W - PAD_L - PAD_R
  const innerH = H - PAD_T - PAD_B
  const n = history.length
  const x = (i: number) => PAD_L + (n === 1 ? innerW / 2 : (i / (n - 1)) * innerW)
  const y = (v: number) => PAD_T + innerH - (Math.max(0, Math.min(100, v)) / 100) * innerH

  const line = (pick: (h: NocHistoryPoint) => number | null) => {
    const pts: string[] = []
    let open = false
    history.forEach((h, i) => {
      const v = pick(h)
      if (v == null || !Number.isFinite(v)) { open = false; return }
      pts.push(`${open ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`)
      open = true
    })
    return pts.join(' ')
  }

  const txPath = line((h) => h.txUtilization)
  const rxPath = line((h) => h.rxUtilization)

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label={t('nocTopo.detail.history')}>
        {[0, 50, 100].map((v) => (
          <g key={v}>
            <line x1={PAD_L} y1={y(v)} x2={W - PAD_R} y2={y(v)} stroke={WH.cardBorder} strokeWidth={1} strokeDasharray={v ? '3 3' : undefined} />
            <text x={PAD_L - 4} y={y(v) + 3} textAnchor="end" fontSize={8} fill={WH.textDim}>{v}</text>
          </g>
        ))}
        <path d={txPath} fill="none" stroke={WH.teal} strokeWidth={1.6} />
        <path d={rxPath} fill="none" stroke={WH.blue} strokeWidth={1.4} strokeDasharray="4 3" />
        <text x={PAD_L} y={H - 5} fontSize={8} fill={WH.textDim}>{history[0].period}</text>
        <text x={W - PAD_R} y={H - 5} fontSize={8} fill={WH.textDim} textAnchor="end">
          {history[n - 1].period}
        </text>
      </svg>
      <div style={{ display: 'flex', gap: 14, fontSize: 11, color: WH.textMuted }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <span style={{ width: 14, borderTop: `2px solid ${WH.teal}`, display: 'inline-block' }} />TX
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <span style={{ width: 14, borderTop: `2px dashed ${WH.blue}`, display: 'inline-block' }} />RX
        </span>
      </div>
    </div>
  )
}
