/* ============================================================================
 * PRTG 看板的自绘 SVG 折线图（不引入图表依赖、不嵌 chart.png）
 * ----------------------------------------------------------------------------
 * 两张图共用同一套坐标 / 网格 / 轴标签实现：
 *   PrtgTrendChart  —— 全网速率趋势（overview.trend[].total_mbps）
 *   PrtgSeriesChart —— 单链路 In / Out / Total 时序（详情接口 series，原始 byte/s
 *                      由调用方先换算成 Mbps 再传进来）
 *
 * SVG 用 isDark 取实值（与 ECharts 的取色约定一致：canvas / SVG 图元不解析
 * CSS 变量时不要写 WH.xxx），保证深浅两套主题下图例与网格都看得清。
 * ========================================================================== */

import { useI18n } from '../../i18n'
import { Empty } from 'antd'
import { formatDateTime } from '../../datetime'
import type { PrtgTrendPoint, PrtgWindow } from './prtgModels'

interface ChartColors {
  line: string
  grid: string
  axis: string
  text: string
}

function colors(isDark: boolean): ChartColors {
  return {
    line: isDark ? '#00d9ff' : '#0d9488',
    grid: isDark ? 'rgba(255,255,255,0.14)' : 'rgba(0,0,0,0.10)',
    axis: isDark ? 'rgba(255,255,255,0.28)' : 'rgba(0,0,0,0.22)',
    text: isDark ? '#aebbd4' : '#3f5468',
  }
}

/** 取一个「好看」的 Y 轴上界：1 / 2 / 5 × 10^n */
function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1
  const exp = Math.floor(Math.log10(value))
  const base = Math.pow(10, exp)
  const n = value / base
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10
  return step * base
}

/** 轴时间标签：5 分钟 / 24 小时只显示时分，7/30 天带上月日 */
function axisTime(iso: string, windowKey: PrtgWindow): string {
  const text = formatDateTime(iso, { seconds: false, fallback: '' })
  if (!text) return ''
  return windowKey === '5m' || windowKey === '24h' ? text.slice(11) : text.slice(5)
}

/** 3 个不重叠的 X 轴刻度下标 */
function tickIndexes(n: number): number[] {
  if (n <= 1) return [0]
  const mid = Math.floor((n - 1) / 2)
  return [0, mid, n - 1].filter((v, i, arr) => arr.indexOf(v) === i)
}

interface TrendChartProps {
  points: PrtgTrendPoint[]
  windowKey: PrtgWindow
  isDark: boolean
}

export function PrtgTrendChart({ points, windowKey, isDark }: TrendChartProps) {
  const { t } = useI18n()
  const c = colors(isDark)
  const valid = points.filter((p): p is { t: string; total_mbps: number } => p.total_mbps != null)
  if (!valid.length) return <Empty description={t('prtg.trend.empty')} />

  const W = 900
  const H = 300
  const pad = { l: 62, r: 18, t: 34, b: 38 }
  const plotW = W - pad.l - pad.r
  const plotH = H - pad.t - pad.b
  const maxV = Math.max(...valid.map(p => p.total_mbps), 0)
  const yMax = niceMax(maxV * 1.1)
  const x = (i: number) => pad.l + (valid.length === 1 ? plotW / 2 : (i / (valid.length - 1)) * plotW)
  const y = (v: number) => pad.t + plotH - (v / yMax) * plotH

  const path = valid.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.total_mbps).toFixed(1)}`).join(' ')
  const ticks = tickIndexes(valid.length)

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', height: 'auto', display: 'block' }}
        role="img"
        aria-label={t('prtg.trend.title')}
      >
        {/* 图例：线色块 + 指标名 + 单位 */}
        <g transform={`translate(${pad.l},14)`}>
          <line x1={0} y1={0} x2={20} y2={0} stroke={c.line} strokeWidth={2.5} />
          <text x={26} y={4} fill={c.text} fontSize={12}>
            {t('prtg.trend.series')} ({t('prtg.trend.unit')})
          </text>
        </g>

        {/* Y 轴网格与刻度（0 / 25% / 50% / 75% / 100%） */}
        {[0, 1, 2, 3, 4].map(i => {
          const v = (yMax * i) / 4
          const yy = y(v)
          return (
            <g key={`y-${i}`}>
              <line x1={pad.l} y1={yy} x2={W - pad.r} y2={yy} stroke={i === 0 ? c.axis : c.grid} strokeWidth={1} />
              <text x={pad.l - 8} y={yy + 4} textAnchor="end" fill={c.text} fontSize={11}>
                {v >= 100 ? v.toFixed(0) : v.toFixed(1)}
              </text>
            </g>
          )
        })}

        {/* X 轴刻度：首 / 中 / 末 */}
        {ticks.map(i => (
          <g key={`x-${i}`}>
            <line x1={x(i)} y1={pad.t + plotH} x2={x(i)} y2={pad.t + plotH + 4} stroke={c.axis} />
            <text
              x={x(i)}
              y={pad.t + plotH + 18}
              textAnchor={i === 0 ? 'start' : i === valid.length - 1 ? 'end' : 'middle'}
              fill={c.text}
              fontSize={11}
            >
              {axisTime(valid[i].t, windowKey)}
            </text>
          </g>
        ))}

        {/* 折线 */}
        <path d={path} fill="none" stroke={c.line} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />

        {/* 采样点（悬停显示该点时间与值） */}
        {valid.map((p, i) => (
          <circle key={`${p.t}-${i}`} cx={x(i)} cy={y(p.total_mbps)} r={2.6} fill={c.line}>
            <title>{`${formatDateTime(p.t, { fallback: '' })} · ${p.total_mbps.toFixed(2)} ${t('prtg.trend.unit')}`}</title>
          </circle>
        ))}
      </svg>
      <div style={{ fontSize: 11, color: c.text, marginTop: 4 }}>
        {t('prtg.trend.points', { n: valid.length })}
      </div>
    </div>
  )
}

export interface SeriesDef {
  key: string
  label: string
  color: string
  points: { t: string; value: number | null }[]
}

interface SeriesChartProps {
  series: SeriesDef[]
  windowKey: PrtgWindow
  isDark: boolean
  emptyText: string
}

/** 多序列折线：按时间并集画，缺点的位置断开而不是连过去 */
export function PrtgSeriesChart({ series, windowKey, isDark, emptyText }: SeriesChartProps) {
  const { t } = useI18n()
  const c = colors(isDark)
  const times = [...new Set(series.flatMap(s => s.points.map(p => p.t)))].sort()
  const values = series.flatMap(s => s.points.map(p => p.value)).filter((v): v is number => v != null)
  if (!times.length || !values.length) return <Empty description={emptyText} />

  const W = 900
  const H = 280
  const pad = { l: 62, r: 18, t: 34, b: 38 }
  const plotW = W - pad.l - pad.r
  const plotH = H - pad.t - pad.b
  const yMax = niceMax(Math.max(...values) * 1.1)
  const x = (i: number) => pad.l + (times.length === 1 ? plotW / 2 : (i / (times.length - 1)) * plotW)
  const y = (v: number) => pad.t + plotH - (v / yMax) * plotH

  /** 每个序列：按时间并集走，遇到缺失值就断开（M 重新起笔） */
  const buildPath = (points: { t: string; value: number | null }[]) => {
    const byTime = new Map(points.map(p => [p.t, p.value]))
    let d = ''
    let open = false
    times.forEach((time, i) => {
      const v = byTime.get(time)
      if (v == null) { open = false; return }
      d += `${open ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)} `
      open = true
    })
    return d.trim()
  }

  const ticks = tickIndexes(times.length)
  const timeIndex = new Map(times.map((time, i) => [time, i]))

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', height: 'auto', display: 'block' }}
        role="img"
        aria-label={t('prtg.detail.series')}
      >
        {/* 图例 */}
        <g transform={`translate(${pad.l},14)`}>
          {series.map((s, i) => (
            <g key={s.key} transform={`translate(${i * 120},0)`}>
              <line x1={0} y1={0} x2={18} y2={0} stroke={s.color} strokeWidth={2.5} />
              <text x={24} y={4} fill={c.text} fontSize={12}>{s.label}</text>
            </g>
          ))}
        </g>

        {/* Y 轴网格 */}
        {[0, 1, 2, 3, 4].map(i => {
          const v = (yMax * i) / 4
          const yy = y(v)
          return (
            <g key={`y-${i}`}>
              <line x1={pad.l} y1={yy} x2={W - pad.r} y2={yy} stroke={i === 0 ? c.axis : c.grid} strokeWidth={1} />
              <text x={pad.l - 8} y={yy + 4} textAnchor="end" fill={c.text} fontSize={11}>
                {v >= 100 ? v.toFixed(0) : v.toFixed(1)}
              </text>
            </g>
          )
        })}

        {/* X 轴刻度 */}
        {ticks.map(i => (
          <g key={`x-${i}`}>
            <line x1={x(i)} y1={pad.t + plotH} x2={x(i)} y2={pad.t + plotH + 4} stroke={c.axis} />
            <text
              x={x(i)}
              y={pad.t + plotH + 18}
              textAnchor={i === 0 ? 'start' : i === times.length - 1 ? 'end' : 'middle'}
              fill={c.text}
              fontSize={11}
            >
              {axisTime(times[i], windowKey)}
            </text>
          </g>
        ))}

        {/* 各序列折线 + 采样点（单点时也能看见） */}
        {series.map(s => (
          <g key={s.key}>
            <path
              d={buildPath(s.points)}
              fill="none"
              stroke={s.color}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {s.points.map(p => {
              const i = timeIndex.get(p.t)
              if (i == null || p.value == null) return null
              return (
                <circle key={`${s.key}-${p.t}`} cx={x(i)} cy={y(p.value)} r={2.2} fill={s.color}>
                  <title>{`${formatDateTime(p.t, { fallback: '' })} · ${p.value.toFixed(2)} ${t('prtg.trend.unit')}`}</title>
                </circle>
              )
            })}
          </g>
        ))}
      </svg>
    </div>
  )
}
