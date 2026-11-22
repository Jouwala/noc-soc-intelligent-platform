/* ============================================================================
 * CS / PS CN KPI 分组看板（?page=cs_cn_kpi / ?page=ps_cn_kpi）
 * ----------------------------------------------------------------------------
 * 数据源：后端 /api/cn-kpi-split/{cs|ps}/overview
 *   → services/cn/cn_kpi_split_service.py（远端 db_ps_cs_cn 只读 SQL API）
 *   → 组件清单与 SQL 见 services/cn/cn_kpi_split_widgets.py
 *
 * 页面结构：日期区间 + 两段（EMS 人工导出 / UME 自动同步）× 若干图表卡。
 * 与 cn_kpi 页的关键区别：**不换源**。EMS 段只读 *_EMS 表、UME 段只读 *_UME 表，
 * 表缺了就空态并写明表名，不拿另一条流水线顶替（两个页面要回答的正是
 * 「这个数来自哪条流水线」）。
 *
 * 约定：卡片走 components/cards，导出走 utils/excelReport，口径说明默认收起
 * （CalcChainNote），卡头 ⓘ 说明数据表/粒度/SQL（AGENTS.md 三.3b / 三.3c）。
 * ========================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Col, DatePicker, Empty, Row, Space, Spin, Tag, Typography } from 'antd'
import { DownloadOutlined, InfoCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import ReactECharts from '../../components/ECharts'
import { CardBase } from '../../components/cards'
import CalcChainNote from '../../components/CalcChainNote'
import { ExplainLabel, ExplainPopover } from '../../components/MetricExplain'
import { exportReport, type ReportSheetSpec } from '../../utils/excelReport'
import { getCnKpiSplitOverview } from '../../api'
import { useI18n } from '../../i18n'
import { formatDateTime } from '../../datetime'
import { useTheme } from '../../theme'
import {
  buildSplitOption, catLabel, emptyReason, widgetExplainText,
  type SplitOverview, type SplitWidget,
} from './cnkpiSplitHelpers'

const { Text } = Typography
const { RangePicker } = DatePicker

/** 单个图表卡：图表 / 空态 / 错误三种状态 */
function WidgetCard({ w, height }: { w: SplitWidget; height: number }) {
  const { isDark } = useTheme()
  const { t } = useI18n()
  const option = useMemo(
    () => (w.empty || w.error || w.rows.length === 0 ? null : buildSplitOption(w, isDark, t)),
    [w, isDark, t],
  )
  return (
    <CardBase
      size="small"
      style={{ height, display: 'flex', flexDirection: 'column' }}
      bodyStyle={{ flex: 1, minHeight: 0, padding: 8, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
      title={(
        <ExplainLabel
          label={<span style={{ fontSize: 13 }}>{w.title}</span>}
          text={[widgetExplainText(w, t)].filter(Boolean).join('\n')}
        />
      )}
      extra={(
        <Space size={4}>
          {w.pct ? <Tag color="cyan" style={{ marginInlineEnd: 0 }}>%</Tag> : null}
          {w.fixed_range ? (
            <Tag color="purple" style={{ marginInlineEnd: 0 }}>{t('cnkpiSplit.monthlyTag')}</Tag>
          ) : null}
          {w.truncated ? (
            <Tag color="error" style={{ marginInlineEnd: 0 }}>{t('cnkpiSplit.truncatedTag')}</Tag>
          ) : null}
        </Space>
      )}
    >
      {w.error ? (
        <Alert type="error" showIcon message={t('cnkpiSplit.queryFail')} description={w.error} style={{ fontSize: 12 }} />
      ) : w.empty ? (
        <div style={{ margin: 'auto', maxWidth: 320, textAlign: 'center' }}>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<span style={{ fontSize: 12 }}>{emptyReason(w, t)}</span>}
          />
        </div>
      ) : !option ? (
        <Empty description={t('cnkpiSplit.empty.noData')} style={{ margin: 'auto' }} />
      ) : (
        <div style={{ flex: '1 1 auto', minHeight: 0 }}>
          <ReactECharts option={option} notMerge style={{ width: '100%', height: '100%' }} opts={{ renderer: 'canvas' }} />
        </div>
      )}
    </CardBase>
  )
}

/** 分段标题：EMS / UME，各带一句来源说明与空态计数（数据状态，常驻可见） */
function SectionHeader({ sectionKey, total, empty }: { sectionKey: 'ems' | 'ume'; total: number; empty: number }) {
  const { t } = useI18n()
  const { isDark } = useTheme()
  const isEms = sectionKey === 'ems'
  return (
    <div
      style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        margin: '4px 0 10px', paddingLeft: 10,
        borderLeft: `3px solid ${isEms ? (isDark ? '#d4a72c' : '#b7791f') : (isDark ? '#2b8cff' : '#2563eb')}`,
      }}
    >
      <Tag color={isEms ? 'gold' : 'blue'} style={{ marginInlineEnd: 0, fontWeight: 600 }}>
        {isEms ? 'EMS' : 'UME'}
      </Tag>
      <Text strong style={{ fontSize: 14 }}>
        {isEms ? t('cnkpiSplit.sec.ems.title') : t('cnkpiSplit.sec.ume.title')}
      </Text>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {isEms ? t('cnkpiSplit.sec.ems.desc') : t('cnkpiSplit.sec.ume.desc')}
      </Text>
      <Text type="secondary" style={{ fontSize: 12 }}>· {t('cnkpiSplit.widgetCount', { n: total })}</Text>
      {empty > 0 && <Tag color="warning" style={{ marginInlineEnd: 0 }}>{t('cnkpiSplit.emptyCount', { n: empty })}</Tag>}
    </div>
  )
}

export default function CnKpiBoard({ board }: { board: 'cs' | 'ps' }) {
  const { t } = useI18n()
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().subtract(30, 'day'), dayjs()])
  const [data, setData] = useState<SplitOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  // 只认最后一次请求（改日期比响应快时，先发的响应不许盖掉后发的）
  const reqSeq = useRef(0)
  const inflight = useRef(false)
  const dataRef = useRef<SplitOverview | null>(null)
  const rangeRef = useRef(range)
  rangeRef.current = range

  /** silent=true 供顶栏自动刷新：不 setLoading、失败保留上一帧 */
  const load = useCallback(async (opts?: { silent?: boolean; force?: boolean; range?: [Dayjs, Dayjs] }) => {
    const silent = !!opts?.silent
    const r = opts?.range ?? rangeRef.current
    if (silent && !dataRef.current && inflight.current) return
    const seq = ++reqSeq.current
    inflight.current = true
    if (!silent) { setLoading(true); setErr('') }
    try {
      const res = await getCnKpiSplitOverview(board, {
        start: r[0].format('YYYY-MM-DD'),
        end: r[1].format('YYYY-MM-DD'),
        force: !!opts?.force,
      })
      if (seq !== reqSeq.current) return
      dataRef.current = res.data as SplitOverview
      setData(res.data as SplitOverview)
      setErr('')
    } catch (e: unknown) {
      if (seq !== reqSeq.current) return
      if (!silent) setErr(e instanceof Error ? e.message : String(e))
    } finally {
      if (seq === reqSeq.current) {
        inflight.current = false
        setLoading(false)
      }
    }
  }, [board])

  useEffect(() => { void load() }, [load])
  // 顶栏自动刷新：原地静默重取，不重挂载（重挂载会把 data 清掉、整页图集体闪白）
  useEffect(() => {
    const onInplaceRefresh = () => { void load({ silent: true }) }
    document.addEventListener('myapp-refresh', onInplaceRefresh)
    return () => document.removeEventListener('myapp-refresh', onInplaceRefresh)
  }, [load])

  const onRangeChange = useCallback((vals: unknown) => {
    const v = vals as [Dayjs, Dayjs] | null
    if (!v || !v[0] || !v[1]) return
    const next: [Dayjs, Dayjs] = [v[0].startOf('day'), v[1].startOf('day')]
    setRange(next)
    void load({ range: next })
  }, [load])

  const sections = data?.sections ?? []
  const total = sections.reduce((n, s) => n + s.widgets.length, 0)
  const withData = sections.reduce((n, s) => n + s.widgets.filter((w) => !w.empty && !w.error).length, 0)
  const emptyCount = sections.reduce((n, s) => n + s.widgets.filter((w) => w.empty).length, 0)
  const chartHeight = 320

  /** 导出 Excel：总览 + 每个组件一张宽表（[时间, 各序列…]） */
  const exportExcel = useCallback(() => {
    if (!data) return
    const stamp = dayjs().format('YYYYMMDD_HHmmss')
    const sheets: ReportSheetSpec[] = [{
      name: 'Overview',
      title: data.title,
      subtitle: `${t('cnkpiSplit.exportRange')}: ${data.meta.start || ''} ~ ${data.meta.end || ''}`,
      header: ['', ''],
      rows: [
        [t('cnkpiSplit.exportRange'), `${data.meta.start || ''} ~ ${data.meta.end || ''}`],
        [t('cnkpiSplit.exportSource'), `${data.meta.db} @ ${data.meta.host}`],
        [t('cnkpiSplit.exportGenerated'), formatDateTime(new Date(), { zone: true })],
        [],
        ...sections.flatMap((s) => [
          [s.key.toUpperCase(), ''],
          ...s.widgets.map((w) => [
            w.title,
            w.empty ? emptyReason(w, t) : `${w.rows.length} ${t('cnkpiSplit.rows')}`,
          ]),
        ]),
      ] as (string | number)[][],
      columnWidths: [30, 60],
    }]

    for (const s of sections) {
      for (const w of s.widgets) {
        if (w.empty || w.error || w.rows.length === 0) continue
        let header: string[]
        let body: (string | number | null)[][]
        if (w.pivot) {
          // pivot → 宽表：行=时间，列=序列
          const names: string[] = []
          const byCat = new Map<string, Record<string, number | null>>()
          const cats: string[] = []
          for (const r of w.rows) {
            const cat = catLabel(String(r[0] ?? ''), w.grain, t)
            const name = String(r[1] ?? '')
            if (!name || name === 'null' || name === 'undefined') continue
            if (!names.includes(name)) names.push(name)
            if (!byCat.has(cat)) { byCat.set(cat, {}); cats.push(cat) }
            byCat.get(cat)![name] = (r[2] === null || r[2] === undefined || r[2] === '') ? null : Number(r[2])
          }
          header = [t('cnkpiSplit.time'), ...names]
          body = cats.map((c) => [c, ...names.map((n) => byCat.get(c)?.[n] ?? null)])
        } else {
          header = [t('cnkpiSplit.time'), ...w.columns.slice(1)]
          body = w.rows.map((r) => [
            catLabel(String(r[0] ?? ''), w.grain, t),
            ...r.slice(1).map((v) => (v === null || v === undefined || v === '' ? null : Number(v))),
          ])
        }
        sheets.push({
          name: w.id,
          title: w.title,
          subtitle: `${t('cnkpiSplit.exportSource')}: ${w.source_table}`,
          header,
          rows: body as (string | number | null)[][],
          columnWidths: [20, ...header.slice(1).map(() => 18)],
        })
      }
    }
    void exportReport(sheets, `${board.toUpperCase()}_CN_KPI_${stamp}.xlsx`)
  }, [data, sections, board, t])

  return (
    <div style={{ padding: 4 }}>
      {/* 工具条：全局 Header 已有页面标题，页内只放区间 / 统计 / 操作 */}
      <CardBase size="small" style={{ marginBottom: 12 }} bodyStyle={{ padding: '8px 12px' }}>
        <Row align="middle" justify="space-between" gutter={[12, 8]}>
          <Col>
            <Space size={10} wrap>
              <RangePicker
                value={range}
                onChange={onRangeChange}
                allowClear={false}
                disabledDate={(d) => d.isAfter(dayjs(), 'day')}
                presets={[
                  { label: t('cnkpiSplit.range7'), value: [dayjs().subtract(6, 'day'), dayjs()] },
                  { label: t('cnkpiSplit.range14'), value: [dayjs().subtract(13, 'day'), dayjs()] },
                  { label: t('cnkpiSplit.range31'), value: [dayjs().subtract(30, 'day'), dayjs()] },
                ]}
                size="small"
              />
              {data?.meta && (
                <ExplainPopover placement="bottom" text={t('cnkpiSplit.rangeHint')}>
                  <Tag icon={<InfoCircleOutlined />} color="blue" style={{ cursor: 'help' }}>
                    {t('cnkpiSplit.summary', { ok: withData, total })}
                  </Tag>
                </ExplainPopover>
              )}
              {data?.meta && (
                <Tag>{data.meta.start} ~ {data.meta.end}</Tag>
              )}
              {data?.meta && data.meta.failed > 0 && (
                <Tag color="error">{t('cnkpiSplit.failed', { n: data.meta.failed })}</Tag>
              )}
              {data?.meta && data.meta.truncated > 0 && (
                <Tag color="warning">{t('cnkpiSplit.truncatedTag')} × {data.meta.truncated}</Tag>
              )}
              {data?.meta?.stale && (
                <Tag color="warning">
                  {t('cnkpiSplit.staleTag', { age: Math.round(data.meta.age_s ?? 0) })}
                </Tag>
              )}
            </Space>
          </Col>
          <Col>
            <Space wrap>
              <Button icon={<ReloadOutlined />} onClick={() => void load({ force: true })} loading={loading} size="small">
                {t('cnkpiSplit.refresh')}
              </Button>
              <Button icon={<DownloadOutlined />} onClick={exportExcel} disabled={!data || loading} size="small">
                {t('cnkpiSplit.export')}
              </Button>
            </Space>
          </Col>
        </Row>
      </CardBase>

      {err && (
        <Alert
          type="error"
          showIcon
          message={t('cnkpiSplit.loadFail')}
          description={<Text>{err}</Text>}
          style={{ marginBottom: 12 }}
        />
      )}

      {/* 整页口径：默认收起（AGENTS.md 三.3b）；空态/失败是「状态」，仍常驻在卡片里 */}
      <div style={{ marginBottom: 12 }}>
        <CalcChainNote
          title={t('cnkpiSplit.note.title')}
          sections={[
            { title: t('cnkpiSplit.note.secSource'), lines: [t('cnkpiSplit.note.source1'), t('cnkpiSplit.note.source2'), t('cnkpiSplit.note.source3')] },
            { title: t('cnkpiSplit.note.secGroup'), lines: [t('cnkpiSplit.note.group1'), t('cnkpiSplit.note.group2')] },
            { title: t('cnkpiSplit.note.secGran'), lines: [t('cnkpiSplit.note.gran1'), t('cnkpiSplit.note.gran2'), t('cnkpiSplit.note.gran3')] },
            { title: t('cnkpiSplit.note.secCarrier'), lines: [t('cnkpiSplit.note.cap1'), t('cnkpiSplit.note.cap2')] },
            { title: t('cnkpiSplit.note.secMissing'), lines: [t('cnkpiSplit.note.dns1')] },
          ]}
        />
      </div>

      {loading && !data ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      ) : (
        <>
          {sections.map((s) => {
            const secEmpty = s.widgets.filter((w) => w.empty).length
            return (
              <div key={s.key} style={{ marginBottom: 18 }}>
                <SectionHeader sectionKey={s.key} total={s.widgets.length} empty={secEmpty} />
                <Row gutter={[12, 12]}>
                  {s.widgets.map((w) => (
                    <Col key={w.id} xs={24} md={12} xl={8}>
                      <WidgetCard w={w} height={chartHeight} />
                    </Col>
                  ))}
                </Row>
              </div>
            )
          })}
          {data && total > 0 && emptyCount === total && (
            <Alert
              type="warning"
              showIcon
              message={t('cnkpiSplit.allEmpty')}
              style={{ marginTop: 8 }}
            />
          )}
        </>
      )}
    </div>
  )
}
