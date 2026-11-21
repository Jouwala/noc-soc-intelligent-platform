/* ==================== TT 州维度统计（page=tt_state） ====================
 * 数据源：`eflow_tt` 里 `wo_no` 非空的单（一行 = 一张派出了 WO 的 TT）。
 * 版式：时间窗 + 粒度（日/周/月）+ 州筛选 → 四张 KPI 卡 → 「州 × 时间桶」矩阵。
 * 每格三个数字，顺序固定为 **告警未清除 / WO 未关闭 / 总数**：
 *   - 告警未清除 = `alarm_clear_time` 为空；
 *   - WO 未关闭   = `eflow_wo.status` 不以 Closed 开头（关联不到 WO 时按未关闭计）；
 *   - 总数        = 窗口内该州该桶的行数。
 * 点任意格子/合计/卡片都下钻，下钻与概览**同一份取数**（后端 service 保证），
 * 所以表格数字与弹窗行数严格相等。
 *
 * 口径细则见 backend/services/tt_state_matrix.py 的模块 docstring；
 * 页面上的「口径说明」默认收起（根 AGENTS.md 四.10），算不全的告警常驻（四.10 例外条）。
 * ================================================================ */
import { useCallback, useEffect, useMemo, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { Alert, Button, Col, DatePicker, Row, Segmented, Select, Space, Spin, Typography } from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'

import { useI18n } from '../i18n'
import { CardBase, StatCard } from '../components/cards'
import CalcChainNote from '../components/CalcChainNote'
import { ExplainIcon } from '../components/MetricExplain'
import { exportAoaReport, exportReport } from '../utils/excelReport'
import { getTtStateDrilldown, getTtStateOverview } from '../api'
import TtStateMatrixTable from './ttState/TtStateMatrixTable'
import TtStateDrillModal from './ttState/TtStateDrillModal'
import {
  ALL_STATES, ZERO, cellKey, pct, stateLabel, sumStats, windowLabel,
  type CellStat, type DrillFlag, type DrillResult, type DrillRow, type Gran,
  type Overview, type Preset, type StatTotals,
} from './ttState/ttStateTypes'

const { RangePicker } = DatePicker
const { Text } = Typography

const PRESET_DAYS: Record<string, number> = { last7: 7, last30: 30, last90: 90, last180: 180 }

export default function TtStateMatrix() {
  const { t, locale } = useI18n()

  /* ---------- 筛选条件 ---------- */
  const [preset, setPreset] = useState<Preset>('last30')
  const [customRange, setCustomRange] = useState<any>(null)
  const [gran, setGran] = useState<Gran>('day')
  const [stateFilter, setStateFilter] = useState<string[]>([])

  /* ---------- 数据 / 加载 / 错误：三件套是页面标配 ---------- */
  const [data, setData] = useState<Overview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  /* ---------- 下钻 ---------- */
  const [drillOpen, setDrillOpen] = useState(false)
  const [drillLoading, setDrillLoading] = useState(false)
  const [drillData, setDrillData] = useState<DrillResult | null>(null)
  const [drillTitle, setDrillTitle] = useState('')
  const [drillAnchor, setDrillAnchor] = useState<{ x: number; y: number } | null>(null)

  /* ---------- 时间窗（与 page=tt 同一套档位） ---------- */
  const win = useMemo(() => {
    if (preset === 'all') return { all: true, start: '', end: '' }
    if (preset === 'custom') {
      if (!customRange?.[0] || !customRange?.[1]) return null
      return {
        all: false,
        start: customRange[0].format('YYYY-MM-DD'),
        end: customRange[1].format('YYYY-MM-DD'),
      }
    }
    const end = dayjs()
    return {
      all: false,
      start: end.subtract(PRESET_DAYS[preset] || 30, 'day').format('YYYY-MM-DD'),
      end: end.format('YYYY-MM-DD'),
    }
  }, [preset, customRange])

  const load = useCallback(async () => {
    if (!win) return
    setLoading(true)
    setError('')
    try {
      const params: Record<string, string | number> = { gran }
      if (win.all) params.all = 1
      else { params.start = win.start; params.end = win.end }
      const res = await getTtStateOverview(params)
      const payload = res.data as Overview
      if (payload?.error) throw new Error(payload.error)
      setData(payload)
    } catch (e: any) {
      setError(e?.message || String(e))
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [win, gran])

  useEffect(() => { void load() }, [load])

  // 切到「自定义」但还没选区间时，先落一个近 30 天的默认区间：
  // 否则 RangePicker 是空的、win 为 null，页面会一直停在上一档的数据上。
  useEffect(() => {
    if (preset === 'custom' && !customRange?.[0]) {
      setCustomRange([dayjs().subtract(30, 'day'), dayjs()])
    }
  }, [preset, customRange])

  /* ---------- 派生：筛选后的州、格子索引、KPI 合计 ---------- */
  const allStates = data?.states || []
  const states: StatTotals[] = useMemo(
    () => (stateFilter.length ? allStates.filter(s => stateFilter.includes(s.state)) : allStates),
    [allStates, stateFilter],
  )
  const periods = data?.periods || []
  const cells = useMemo(() => {
    const map = new Map<string, CellStat>()
    for (const c of data?.cells || []) map.set(cellKey(c.state, c.period), c)
    return map
  }, [data])
  const totals = useMemo(() => sumStats(states), [states])

  const stateOptions = useMemo(
    () => allStates.map(s => ({ value: s.state, label: stateLabel(s.state, t) })),
    [allStates, t],
  )

  /* ---------- 下钻：概览与明细共用同一时间窗 / 粒度 / 州 ---------- */
  const openDrill = useCallback(async (
    e: ReactMouseEvent, state: string, period: string, flag: DrillFlag = '',
  ) => {
    if (!win) return
    setDrillAnchor({ x: e.clientX, y: e.clientY })
    setDrillOpen(true)
    setDrillLoading(true)
    setDrillData(null)

    const flagLabel = flag === 'alarm_uncleared' ? t('ttState.kpi.alarmUncleared')
      : flag === 'wo_unclosed' ? t('ttState.kpi.woUnclosed')
      : flag === 'tt_unclosed' ? t('ttState.kpi.ttUnclosed')
      : flag === 'alarm_cleared' ? t('ttState.kpi.alarmCleared')
      : ''
    setDrillTitle(
      state === ALL_STATES
        ? (flagLabel ? t('ttState.drillTitle.kpi', { label: flagLabel }) : t('ttState.drillTitle.all'))
        : `${stateLabel(state, t)} · ${period || t('ttState.allPeriods')}`,
    )

    try {
      const params: Record<string, string | number> = { gran, state }
      if (win.all) params.all = 1
      else { params.start = win.start; params.end = win.end }
      if (period) params.period = period
      if (flag) params.flag = flag
      const res = await getTtStateDrilldown(params)
      const payload = res.data as DrillResult
      if (payload?.error) throw new Error(payload.error)
      setDrillData(payload)
    } catch (err: any) {
      setDrillData({
        meta: { count: 0, returned: 0, truncated: false, limit: 0, warnings: [String(err?.message || err)] },
        rows: [],
        error: String(err?.message || err),
      })
    } finally {
      setDrillLoading(false)
    }
  }, [win, gran, t])

  const granLabelKey = gran === 'day' ? 'ttwo.gran.day' : gran === 'week' ? 'ttwo.gran.week' : 'ttwo.gran.month'

  /* ---------- 导出 ---------- */
  const exportOverview = useCallback(() => {
    if (!data) return
    const stamp = dayjs().format('YYYYMMDD_HHmmss')
    const periodHeader = [t('ttState.export.state'), t('ttState.export.period'),
      t('ttState.export.alarm'), t('ttState.export.wo'), t('ttState.export.tt'),
      t('ttState.export.total')]
    const detailRows = states.flatMap(st => periods.map(p => {
      const c = cells.get(cellKey(st.state, p)) || ZERO
      return [stateLabel(st.state, t), p, c.alarm_uncleared, c.wo_unclosed, c.tt_unclosed,
        c.total] as (string | number)[]
    }))
    const summaryRows = states.map(st => [
      stateLabel(st.state, t), st.alarm_uncleared, st.wo_unclosed, st.tt_unclosed,
      st.total, st.alarm_cleared,
    ])
    void exportReport([
      {
        name: t('ttState.export.detailSheet'),
        title: `${t('ttState.matrixTitle')} · ${windowLabel(data.meta, t)} · ${t(granLabelKey)}`,
        header: periodHeader,
        rows: detailRows,
        totalRow: [t('ttState.row.total'), '', totals.alarm_uncleared, totals.wo_unclosed,
          totals.tt_unclosed, totals.total],
        numericCols: [2, 3, 4, 5],
      },
      {
        name: t('ttState.export.summarySheet'),
        title: `${t('ttState.kpiTitle')} · ${windowLabel(data.meta, t)}`,
        header: [t('ttState.export.state'), t('ttState.export.alarm'), t('ttState.export.wo'),
          t('ttState.export.tt'), t('ttState.export.total'), t('ttState.export.alarmCleared')],
        rows: summaryRows,
        totalRow: [t('ttState.row.total'), totals.alarm_uncleared, totals.wo_unclosed,
          totals.tt_unclosed, totals.total, totals.alarm_cleared],
        numericCols: [1, 2, 3, 4, 5],
      },
    ], `tt_state_${gran}_${stamp}.xlsx`)
  }, [data, states, periods, cells, totals, granLabelKey, t])

  const exportDrill = useCallback((rows: DrillRow[]) => {
    if (!rows.length) return
    void exportAoaReport({
      filename: `tt_state_drill_${dayjs().format('YYYYMMDD_HHmmss')}.xlsx`,
      sheetName: t('ttState.export.drillSheet'),
      title: drillTitle,
      header: [t('ttState.drill.col.tt'), t('ttState.drill.col.wo'), t('ttState.export.state'),
        t('ttState.drill.col.site'), t('ttState.drill.col.occur'), t('ttState.drill.col.clear'),
        t('ttState.drill.col.ttStatus'), t('ttState.drill.col.woStatus'),
        t('ttState.tag.alarm'), t('ttState.tag.wo'), t('ttState.tag.tt')],
      rows: rows.map(r => [
        r.tt_no, r.wo_no, stateLabel(r.state, t), r.site_name, r.alarm_occur_time,
        r.alarm_clear_time, r.tt_status, r.wo_status,
        r.alarm_uncleared ? 1 : 0, r.wo_closed ? 0 : 1, r.tt_unclosed ? 1 : 0,
      ]),
      numericCols: [8, 9, 10],
    })
  }, [drillTitle, t])

  /* ---------- 口径说明（默认收起） ---------- */
  const calcSections = useMemo(() => [
    { title: t('ttState.calc.sourceTitle'), lines: [t('ttState.calc.source'), t('ttState.calc.woJoin')] },
    { title: t('ttState.calc.timeTitle'), lines: [t('ttState.calc.time'), t('ttState.calc.bucket')] },
    {
      title: t('ttState.calc.metricTitle'),
      lines: [t('ttState.calc.metricA'), t('ttState.calc.metricB'), t('ttState.calc.metricC'),
        t('ttState.calc.metricD')],
    },
    { title: t('ttState.calc.noteTitle'), lines: [t('ttState.calc.note1'), t('ttState.calc.note2')] },
  ], [t])

  const fmtPct = (part: number, whole: number) => t('ttState.pctOfTotal', { p: pct(part, whole) })
  const kpiValue = (v: number) => (data ? v : '—')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>
      {/* ① 筛选条 */}
      <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
        <Space wrap align="center" size="middle">
          <Segmented
            className="wh-seg"
            options={[
              { label: t('ttwo.allTime'), value: 'all' },
              { label: t('ttwo.last7Days'), value: 'last7' },
              { label: t('ttwo.last30Days'), value: 'last30' },
              { label: t('ttwo.last3Months'), value: 'last90' },
              { label: t('ttwo.last6Months'), value: 'last180' },
              { label: t('ttwo.custom'), value: 'custom' },
            ]}
            value={preset}
            onChange={v => setPreset(v as Preset)}
            style={{ background: 'var(--wh-seg-bg)' }}
          />
          {preset === 'custom' && (
            <RangePicker
              value={customRange}
              onChange={setCustomRange}
              allowClear={false}
            />
          )}
          <Segmented
            className="wh-seg"
            options={[
              { label: t('ttwo.gran.day'), value: 'day' },
              { label: t('ttwo.gran.week'), value: 'week' },
              { label: t('ttwo.gran.month'), value: 'month' },
            ]}
            value={gran}
            onChange={v => setGran(v as Gran)}
            style={{ background: 'var(--wh-seg-bg)' }}
          />
          <Select
            mode="multiple"
            allowClear
            maxTagCount="responsive"
            value={stateFilter}
            onChange={setStateFilter}
            placeholder={t('ttState.allStates')}
            options={stateOptions}
            style={{ minWidth: 220, maxWidth: 320 }}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            {t('common.refresh')}
          </Button>
          <Button icon={<DownloadOutlined />} onClick={exportOverview} disabled={!data}>
            {t('common.exportExcel')}
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>{windowLabel(data?.meta, t)}</Text>
        </Space>
      </CardBase>

      {/* ② 错误 / 算不全：常驻可见（根 AGENTS.md 四.10 的「异常」例外） */}
      {error && <Alert type="error" showIcon message={t('ttState.loadFailed', { msg: error })} />}
      {!!data?.meta?.wo_missing && (
        <Alert type="warning" showIcon message={t('ttState.warn.woMissing', { n: data.meta.wo_missing })} />
      )}
      {!!data?.meta?.skipped_rows && (
        <Alert type="warning" showIcon message={t('ttState.warn.skipped', { n: data.meta.skipped_rows })} />
      )}

      {/* ③ KPI 卡：数字可下钻、可悬停看构成 */}
      <Row gutter={[12, 12]}>
        <Col flex="1 1 15rem">
          <StatCard
            title={<Space size={4}>{t('ttState.kpi.total')}<ExplainIcon text={t('ttState.explain.total')} /></Space>}
            value={kpiValue(totals.total)}
            accentColor="var(--wh-teal)"
            explainText={t('ttState.explain.total')}
            onClick={e => void openDrill(e, ALL_STATES, '')}
          />
        </Col>
        <Col flex="1 1 15rem">
          <StatCard
            title={<Space size={4}>{t('ttState.kpi.woUnclosed')}<ExplainIcon text={t('ttState.explain.woUnclosed')} /></Space>}
            value={kpiValue(totals.wo_unclosed)}
            accentColor="var(--wh-warn)"
            subText={fmtPct(totals.wo_unclosed, totals.total)}
            explainText={t('ttState.explain.woUnclosed')}
            onClick={e => void openDrill(e, ALL_STATES, '', 'wo_unclosed')}
          />
        </Col>
        <Col flex="1 1 15rem">
          <StatCard
            title={<Space size={4}>{t('ttState.kpi.ttUnclosed')}<ExplainIcon text={t('ttState.explain.ttUnclosed')} /></Space>}
            value={kpiValue(totals.tt_unclosed)}
            accentColor="var(--wh-indigo)"
            subText={fmtPct(totals.tt_unclosed, totals.total)}
            explainText={t('ttState.explain.ttUnclosed')}
            onClick={e => void openDrill(e, ALL_STATES, '', 'tt_unclosed')}
          />
        </Col>
        <Col flex="1 1 15rem">
          <StatCard
            title={<Space size={4}>{t('ttState.kpi.alarmUncleared')}<ExplainIcon text={t('ttState.explain.alarmUncleared')} /></Space>}
            value={kpiValue(totals.alarm_uncleared)}
            accentColor="var(--wh-danger)"
            subText={fmtPct(totals.alarm_uncleared, totals.total)}
            explainText={t('ttState.explain.alarmUncleared')}
            onClick={e => void openDrill(e, ALL_STATES, '', 'alarm_uncleared')}
          />
        </Col>
        <Col flex="1 1 15rem">
          <StatCard
            title={<Space size={4}>{t('ttState.kpi.alarmCleared')}<ExplainIcon text={t('ttState.explain.alarmCleared')} /></Space>}
            value={kpiValue(totals.alarm_cleared)}
            accentColor="var(--wh-ok)"
            subText={fmtPct(totals.alarm_cleared, totals.total)}
            explainText={t('ttState.explain.alarmCleared')}
            onClick={e => void openDrill(e, ALL_STATES, '', 'alarm_cleared')}
          />
        </Col>
      </Row>

      {/* ④ 矩阵 */}
      <CardBase
        title={<Space size={6} wrap>{t('ttState.matrixTitle')}<ExplainIcon text={t('ttState.explain.matrix')} /></Space>}
        extra={
          <div className="tts-legend">
            <span className="tts-legend-chip"><i className="tts-legend-dot tts-legend-a" />{t('ttState.legend.alarm')}</span>
            <span className="tts-legend-chip"><i className="tts-legend-dot tts-legend-b" />{t('ttState.legend.wo')}</span>
            <span className="tts-legend-chip"><i className="tts-legend-dot tts-legend-t" />{t('ttState.legend.tt')}</span>
            <span className="tts-legend-chip"><i className="tts-legend-dot tts-legend-c" />{t('ttState.legend.total')}</span>
            <Text type="secondary" style={{ fontSize: 12 }}>{t('ttState.matrixHint')}</Text>
          </div>
        }
      >
        <Spin spinning={loading}>
          <TtStateMatrixTable
            states={states}
            periods={periods}
            cells={cells}
            gran={gran}
            onCell={(e, st, p) => void openDrill(e, st, p)}
            onStateTotal={(e, st) => void openDrill(e, st, '')}
            onPeriodTotal={(e, p) => void openDrill(e, ALL_STATES, p)}
            onGrandTotal={e => void openDrill(e, ALL_STATES, '')}
          />
        </Spin>
      </CardBase>

      {/* ⑤ 口径说明（默认收起） */}
      <CalcChainNote title={t('ttState.calc.title')} sections={calcSections} />

      {/* ⑥ 下钻明细 */}
      <TtStateDrillModal
        open={drillOpen}
        anchor={drillAnchor}
        loading={drillLoading}
        data={drillData}
        title={drillTitle}
        onClose={() => setDrillOpen(false)}
        onExport={exportDrill}
      />
    </div>
  )
}
