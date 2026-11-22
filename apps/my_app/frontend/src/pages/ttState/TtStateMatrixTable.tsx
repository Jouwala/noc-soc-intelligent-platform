/* ==================== TT 州维度统计：矩阵表 ====================
 * 行 = 州（含「合计」行），列 = 日/周/月时间桶（含「合计」列）。
 * 每格显示「告警未清除 / WO 未关闭 / TT 未关闭 / 总数」四个数（顺序与图例一致）；
 * 点格子下钻该「州 × 时间桶」，点行/列合计下钻该州全部时间 / 全部州该时段。
 * ================================================================ */
import { useEffect, useRef, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react'
import { Empty } from 'antd'

import { numLocale, useI18n } from '../../i18n'
import { ExplainPopover } from '../../components/MetricExplain'
import {
  ZERO, cellKey, periodHead, stateLabel, sumStats,
  type CellStat, type Gran, type StatTotals,
} from './ttStateTypes'
import './ttState.css'

interface Props {
  states: StatTotals[]
  periods: string[]
  cells: Map<string, CellStat>
  gran: Gran
  onCell: (e: ReactMouseEvent, state: string, period: string) => void
  onStateTotal: (e: ReactMouseEvent, state: string) => void
  onPeriodTotal: (e: ReactMouseEvent, period: string) => void
  onGrandTotal: (e: ReactMouseEvent) => void
}

export default function TtStateMatrixTable({
  states, periods, cells, gran, onCell, onStateTotal, onPeriodTotal, onGrandTotal,
}: Props) {
  const { t, locale } = useI18n()
  const scrollRef = useRef<HTMLDivElement | null>(null)

  // 新数据到了先滚到最右：矩阵按时间升序，最新的桶在右边 —— 用户进来第一眼
  // 应该看到「最近」，而不是几百列前的窗口起点。
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollLeft = el.scrollWidth
  }, [periods.length, gran, states.length])

  if (!states.length || !periods.length) {
    return <div className="tts-empty"><Empty description={t('ttState.empty')} /></div>
  }

  const fmt = (n: number) => n.toLocaleString(numLocale(locale))
  const total = sumStats(states)
  // 四格数字比原来的三格宽：列宽同步放大，否则千位数会被挤成两行
  const cols = `10rem repeat(${periods.length}, minmax(9.2rem, 1fr)) 9.2rem`

  return (
    <div className="tts-scroll" ref={scrollRef}>
      <div className="tts-grid" style={{ gridTemplateColumns: cols }} role="table" aria-label={t('ttState.matrixTitle')}>
        {/* 表头：首列固定，右侧每列一个时间桶，最后一列合计 */}
        <div className="tts-th tts-sticky" role="columnheader">
          <span className="tts-th-main">{t('ttState.col.state')}</span>
        </div>
        {periods.map(p => {
          const h = periodHead(p, gran)
          return (
            <div className="tts-th" key={p} role="columnheader">
              <span className="tts-th-main">{h.main}</span>
              {h.sub && <span className="tts-th-sub">{h.sub}</span>}
            </div>
          )
        })}
        <div className="tts-th" role="columnheader">
          <span className="tts-th-main">{t('ttState.col.total')}</span>
        </div>

        {/* 数据行 */}
        {states.map(st => {
          const label = stateLabel(st.state, t)
          return (
            <RowCells key={st.state}>
              <div className="tts-state tts-sticky" role="rowheader">
                <span className="tts-state-dot" />
                {label}
              </div>
              {periods.map(p => {
                const c = cells.get(cellKey(st.state, p)) || ZERO
                const explain = t('ttState.cell.explain', {
                  state: label, period: p,
                  a: c.alarm_uncleared, b: c.wo_unclosed, c: c.tt_unclosed, d: c.total,
                })
                return (
                  <div className="tts-cellwrap" key={p} role="cell">
                    <Quad
                      a={c.alarm_uncleared} b={c.wo_unclosed} c={c.tt_unclosed} d={c.total}
                      fmt={fmt} explain={explain}
                      onClick={e => onCell(e, st.state, p)}
                    />
                  </div>
                )
              })}
              <div className="tts-rowtotal" role="cell">
                <Quad
                  a={st.alarm_uncleared} b={st.wo_unclosed} c={st.tt_unclosed} d={st.total}
                  fmt={fmt}
                  explain={t('ttState.rowTotal.explain', {
                    state: label, a: st.alarm_uncleared, b: st.wo_unclosed,
                    c: st.tt_unclosed, d: st.total,
                  })}
                  onClick={e => onStateTotal(e, st.state)}
                />
              </div>
            </RowCells>
          )
        })}

        {/* 合计行 */}
        <RowCells>
          <div className="tts-state tts-sticky tts-grand" role="rowheader">{t('ttState.row.total')}</div>
          {periods.map(p => {
            const s = states.reduce((acc, st) => {
              const c = cells.get(cellKey(st.state, p)) || ZERO
              acc.a += c.alarm_uncleared
              acc.b += c.wo_unclosed
              acc.c += c.tt_unclosed
              acc.d += c.total
              return acc
            }, { a: 0, b: 0, c: 0, d: 0 })
            return (
              <div className="tts-cellwrap tts-grand" key={p} role="cell">
                <Quad
                  a={s.a} b={s.b} c={s.c} d={s.d} fmt={fmt}
                  explain={t('ttState.periodTotal.explain', { period: p, a: s.a, b: s.b, c: s.c, d: s.d })}
                  onClick={e => onPeriodTotal(e, p)}
                />
              </div>
            )
          })}
          <div className="tts-rowtotal tts-grand" role="cell">
            <Quad
              a={total.alarm_uncleared} b={total.wo_unclosed} c={total.tt_unclosed} d={total.total} fmt={fmt}
              explain={t('ttState.grandTotal.explain', {
                a: total.alarm_uncleared, b: total.wo_unclosed, c: total.tt_unclosed, d: total.total,
              })}
              onClick={onGrandTotal}
            />
          </div>
        </RowCells>
      </div>
    </div>
  )
}

/** 行分组：Grid 里用 display:contents 的行包裹，不额外产生盒子，sticky 子项照常生效 */
function RowCells({ children }: { children: ReactNode }) {
  return <div style={{ display: 'contents' }}>{children}</div>
}

/** 四格数字「告警未清除 / WO 未关闭 / TT 未关闭 / 总数」；有数时悬停给出口径，点开下钻 */
function Quad({
  a, b, c, d, fmt, explain, onClick,
}: {
  a: number; b: number; c: number; d: number
  fmt: (n: number) => string
  explain: string
  onClick: (e: ReactMouseEvent) => void
}) {
  const empty = !a && !b && !c && !d
  const btn = (
    <button
      type="button"
      className={`tts-cell${empty ? ' tts-cell-empty' : ''}`}
      onClick={onClick}
      aria-label={explain}
    >
      <span className="tts-n tts-n-a">{fmt(a)}</span>
      <span className="tts-sep">/</span>
      <span className="tts-n tts-n-b">{fmt(b)}</span>
      <span className="tts-sep">/</span>
      <span className="tts-n tts-n-t">{fmt(c)}</span>
      <span className="tts-sep">/</span>
      <span className="tts-n tts-n-c">{fmt(d)}</span>
    </button>
  )
  return empty ? btn : <ExplainPopover text={explain} placement="top">{btn}</ExplainPopover>
}
