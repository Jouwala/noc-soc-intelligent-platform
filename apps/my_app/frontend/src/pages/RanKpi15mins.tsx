/* ============================================================================
 * RAN KPI 15 分钟看板（?page=ran_kpi_15mins）
 * ----------------------------------------------------------------------------
 * 数据源：后端 /api/ran-kpi-15mins/overview?tech=2G|3G|4G|5G
 *   → services/ran_kpi_assurance/ran_kpi_15mins_service.py（远端 ran_kpi_daily.sqlite3
 *     的 ran_kpi_15mins 长表）
 *   → 由任务 ran_kpi_15mins_sync 每 15 分钟从 RAN UME 取数写入：
 *     2G/3G/4G/5G 四个 NOC Platform 模板，Object Group=all，gr=900。
 *
 * 核心展示：四个页签（2G/3G/4G/5G），每个页签下按模板指标铺卡片；每张卡画
 * **Day0（今天）/ Day1（昨天）/ Day7（7 天前，同一星期几）** 三条曲线，
 * x 轴为 HH:MM（15 分钟一档，一天 96 点）。
 *
 * 页签按需取数：切到哪个制式才打远端，不一次拉 66 张图；每个制式的数据在
 * 前端保留，来回切页签不重复拉（刷新/自动刷新只重取当前页签）。
 *
 * 约定（AGENTS.md 三.3）：卡片走 components/cards，导出走 utils/excelReport，
 * ECharts 用 DARK/LIGHT 实值，口径说明默认收起（CalcChainNote），数字可悬停
 * （ExplainPopover / ExplainIcon），文案走 t()。
 * ========================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Col, Empty, Row, Space, Spin, Tabs, Tag, Typography } from 'antd'
import { DownloadOutlined, InfoCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import ReactECharts from '../components/ECharts'
import { CardBase } from '../components/cards'
import CalcChainNote from '../components/CalcChainNote'
import { ExplainLabel, ExplainPopover } from '../components/MetricExplain'
import { exportReport, type ReportSheetSpec } from '../utils/excelReport'
import { getRanKpi15minsOverview } from '../api'
import { useI18n } from '../i18n'
import { formatDateTime } from '../datetime'
import { useTheme } from '../theme'
import {
  buildDayOption, compareDay0Day1, dayColor, DAY_ORDER, fmtVsPct, titleWithUnit,
  type Overview15, type Widget15,
} from './ranKpi15m/helpers'

const { Text } = Typography
const TECHS = ['2G', '3G', '4G', '5G'] as const
type Tech = (typeof TECHS)[number]

/** 日标签的 i18n key：必须显式 switch 返回字面量（动态拼 key 会被 check:i18n 拦下） */
function dayLabelKey(day: string) {
  switch (day) {
    case 'Day0': return 'ranKpi15.day0'
    case 'Day1': return 'ranKpi15.day1'
    case 'Day7': return 'ranKpi15.day7'
    default: return ''
  }
}
function dayLabel(t: (k: string) => string, day: string) {
  const k = dayLabelKey(day)
  return k ? t(k) : day
}

/** 单个指标卡：图表 / 空态 / 错误三种状态 */
function WidgetCard({ w, height }: { w: Widget15; height: number }) {
  const { isDark } = useTheme()
  const { t } = useI18n()
  const option = useMemo(
    () => (w.empty || w.error || w.rows.length === 0 ? null : buildDayOption(w, isDark)),
    [w, isDark],
  )
  const vs = useMemo(() => (w.empty || w.error ? null : compareDay0Day1(w)), [w])
  const vsColor = vs?.pct != null && Math.abs(vs.pct) > 10 ? 'orange' : 'default'

  return (
    <CardBase
      size="small"
      style={{ height, display: 'flex', flexDirection: 'column' }}
      bodyStyle={{ flex: 1, minHeight: 0, padding: 8, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
      title={(
        <ExplainLabel
          label={(
            <span
              style={{
                fontSize: 13, display: 'block', maxWidth: 260,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}
            >
              {titleWithUnit(w)}
            </span>
          )}
          text={[
            w.empty ? t('ranKpi15.explain.emptyCard', { reason: w.empty_reason || t('ranKpi15.noData') }) : '',
            t('ranKpi15.explain.card', { name: w.metric_name }),
          ].filter(Boolean).join('\n')}
        />
      )}
      extra={(
        <Space size={4}>
          {vs && (vs.pct != null ? (
            <ExplainPopover placement="bottom" text={[t('ranKpi15.vsDay1Hint', { time: vs.last0 }), t('ranKpi15.vsFrontend')].join('\n')}>
              <Tag color={vsColor} style={{ marginInlineEnd: 0, fontSize: 11, cursor: 'help' }}>
                {t('ranKpi15.vsDay1', { pct: fmtVsPct(vs.pct) })}
              </Tag>
            </ExplainPopover>
          ) : (
            <ExplainPopover placement="bottom" text={t('ranKpi15.vsNoBase')}>
              <Tag color="default" style={{ marginInlineEnd: 0, fontSize: 11, cursor: 'help' }}>
                {t('ranKpi15.vsDay1NoBase')}
              </Tag>
            </ExplainPopover>
          ))}
          {w.pct && <Tag color="cyan" style={{ marginInlineEnd: 0 }}>%</Tag>}
        </Space>
      )}
    >
      {w.error ? (
        <Alert type="error" showIcon message={t('ranKpi15.queryFail')} description={w.error} style={{ fontSize: 12 }} />
      ) : w.empty ? (
        <div style={{ margin: 'auto', maxWidth: 320, textAlign: 'center' }}>
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={
            <span style={{ fontSize: 12 }}>{w.empty_reason || t('ranKpi15.noData')}</span>
          } />
        </div>
      ) : !option ? (
        <Empty description={t('ranKpi15.noData')} style={{ margin: 'auto' }} />
      ) : (
        <div style={{ flex: '1 1 auto', minHeight: 0 }}>
          <ReactECharts option={option} notMerge style={{ width: '100%', height: '100%' }} opts={{ renderer: 'canvas' }} />
        </div>
      )}
    </CardBase>
  )
}

export default function RanKpi15mins() {
  const { t } = useI18n()
  const { isDark } = useTheme()
  const [tech, setTech] = useState<Tech>('2G')
  const [dataByTech, setDataByTech] = useState<Partial<Record<Tech, Overview15>>>({})
  const [loadingByTech, setLoadingByTech] = useState<Partial<Record<Tech, boolean>>>({})
  const [errByTech, setErrByTech] = useState<Partial<Record<Tech, string>>>({})
  // 只认每个制式最后一次请求：自动刷新与手动刷新撞车时，先发的响应不许盖后发的
  const seqRef = useRef<Record<string, number>>({})
  const inflightRef = useRef<Record<string, boolean>>({})
  const dataRef = useRef<Partial<Record<Tech, Overview15>>>({})

  const load = useCallback(async (target: Tech, opts?: { silent?: boolean; force?: boolean }) => {
    const silent = !!opts?.silent
    if (silent && !dataRef.current[target] && inflightRef.current[target]) return
    const seq = (seqRef.current[target] ?? 0) + 1
    seqRef.current[target] = seq
    inflightRef.current[target] = true
    if (!silent) {
      setLoadingByTech((m) => ({ ...m, [target]: true }))
      setErrByTech((m) => ({ ...m, [target]: '' }))
    }
    try {
      const res = await getRanKpi15minsOverview({ tech: target, force: !!opts?.force })
      if (seq !== seqRef.current[target]) return
      const d = res.data as Overview15
      dataRef.current = { ...dataRef.current, [target]: d }
      setDataByTech((m) => ({ ...m, [target]: d }))
      setErrByTech((m) => ({ ...m, [target]: '' }))
    } catch (e: unknown) {
      if (seq !== seqRef.current[target]) return
      if (!silent) {
        const msg = e instanceof Error ? e.message : String(e)
        setErrByTech((m) => ({ ...m, [target]: msg }))
      }
    } finally {
      if (seq === seqRef.current[target]) {
        inflightRef.current[target] = false
        setLoadingByTech((m) => ({ ...m, [target]: false }))
      }
    }
  }, [])

  // 首次只取当前页签；其余页签切到时再取（各制式 11~24 张图，一次全拉太重）
  useEffect(() => {
    if (!dataRef.current[tech]) void load(tech)
  }, [tech, load])

  // 顶栏自动刷新：当前页签静默重取，不重挂载整页（否则所有图一起闪白）
  useEffect(() => {
    const onInplaceRefresh = () => { void load(tech, { silent: true }) }
    document.addEventListener('myapp-refresh', onInplaceRefresh)
    return () => document.removeEventListener('myapp-refresh', onInplaceRefresh)
  }, [load, tech])

  const data = dataByTech[tech]
  const loading = !!loadingByTech[tech]
  const err = errByTech[tech] ?? ''
  const widgets = data?.widgets ?? []
  const withData = widgets.filter((w) => !w.empty && !w.error).length
  const chartHeight = 300

  /** 页头图例只列**库里真有数据**的那几天（Day7 要攒够历史才会出现） */
  const presentDays = useMemo(() => {
    const found = new Set<string>()
    for (const w of widgets) for (const r of w.rows) found.add(String(r[1]))
    return DAY_ORDER.filter((d) => found.has(d as string))
  }, [widgets])

  const exportExcel = useCallback(() => {
    if (!data) return
    const stamp = dayjs().format('YYYYMMDD_HHmmss')
    const sheets: ReportSheetSpec[] = [{
      name: 'Overview',
      title: `RAN_KPI_15mins_${tech}`,
      subtitle: `${t('ranKpi15.exportSource')}: ${data.meta.db}.${data.meta.table} · ${data.meta.template}`,
      header: ['', ''],
      rows: [
        [t('ranKpi15.colTech'), tech],
        [t('ranKpi15.colTemplate'), data.meta.template],
        [t('ranKpi15.exportSource'), `${data.meta.db}.${data.meta.table}`],
        [t('ranKpi15.exportGenerated'), formatDateTime(new Date(), { zone: true })],
        [t('ranKpi15.latest'), data.meta.latest || '—'],
        [],
        ...widgets.map((w) => [
          w.title,
          w.empty ? t('ranKpi15.noData') : `${w.rows.length} ${t('ranKpi15.rows')}`,
        ]),
      ] as (string | number)[][],
      columnWidths: [26, 52],
    }]

    for (const w of widgets) {
      if (w.empty || w.error || w.rows.length === 0) continue
      const days = DAY_ORDER.filter((d) => w.rows.some((r) => String(r[1]) === d))
      const byTime = new Map<string, Record<string, number | null>>()
      const times: string[] = []
      for (const r of w.rows) {
        const tt = String(r[0])
        const d = String(r[1])
        if (!byTime.has(tt)) { byTime.set(tt, {}); times.push(tt) }
        byTime.get(tt)![d] = r[2] === null || r[2] === undefined || r[2] === ''
          ? null
          : w.pct ? Math.round(Number(r[2]) * 1000000) / 10000 : Number(r[2])
      }
      const hdr = [t('ranKpi15.time'), ...days.map((d) => `${dayLabel(t, d)} (${titleWithUnit(w)})`)]
      const body = times.map((tt) => [tt, ...days.map((d) => byTime.get(tt)?.[d] ?? null)])
      sheets.push({
        name: w.id,
        title: titleWithUnit(w),
        subtitle: `${t('ranKpi15.exportSource')}: ${data.meta.db}.${data.meta.table}`,
        header: hdr,
        rows: body as (string | number | null)[][],
        columnWidths: [12, ...days.map(() => 22)],
      })
    }
    void exportReport(sheets, `RAN_KPI_15mins_${tech}_${stamp}.xlsx`)
  }, [data, tech, widgets, t])

  return (
    <div style={{ padding: 4 }}>
      {/* 工具条：全局 Header 已显示页面标题，页内只留统计/图例/操作 */}
      <CardBase size="small" style={{ marginBottom: 12 }} bodyStyle={{ padding: '8px 12px' }}>
        <Row align="middle" justify="space-between" gutter={[12, 8]}>
          <Col>
            <Space size={10} wrap>
              <ExplainPopover placement="bottom" text={[t('ranKpi15.legendHint'), t('ranKpi15.tip.summary', { ok: withData, total: widgets.length })].join('\n')}>
                <Tag icon={<InfoCircleOutlined />} color="blue" style={{ cursor: 'help' }}>
                  {t('ranKpi15.summary', { ok: withData, total: widgets.length })}
                </Tag>
              </ExplainPopover>
              {data?.meta && (
                <ExplainPopover placement="bottom" text={t('ranKpi15.tip.elapsed', { ms: data.meta.elapsed_ms, cached: data.meta.cached ? t('ranKpi15.cachedYes') : t('ranKpi15.cachedNo') })}>
                  <Tag style={{ cursor: 'help' }}>{data.meta.elapsed_ms}ms{data.meta.cached ? ` · ${t('ranKpi15.cached')}` : ''}</Tag>
                </ExplainPopover>
              )}
              {data?.meta?.latest && (
                <ExplainPopover placement="bottom" text={t('ranKpi15.tip.latest')}>
                  <Tag color="green" style={{ cursor: 'help' }}>{t('ranKpi15.latest')}: {data.meta.latest}</Tag>
                </ExplainPopover>
              )}
              <Space size={6} align="center" wrap>
                {presentDays.map((d) => (
                  <Space key={d} size={4} align="center">
                    <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: dayColor(d, isDark) }} />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {dayLabel(t, d)}{data?.meta?.days ? ` (${data.meta.days[d === 'Day0' ? 'd0' : d === 'Day1' ? 'd1' : 'd7']})` : ''}
                    </Text>
                  </Space>
                ))}
                {!presentDays.includes('Day7') && (
                  <Text type="secondary" style={{ fontSize: 12 }}>{t('ranKpi15.day7Absent')}</Text>
                )}
              </Space>
            </Space>
          </Col>
          <Col>
            <Space wrap>
              <Button icon={<ReloadOutlined />} onClick={() => load(tech, { force: true })} loading={loading} size="small">
                {t('ranKpi15.refresh')}
              </Button>
              <Button icon={<DownloadOutlined />} onClick={exportExcel} disabled={!data || loading} size="small">
                {t('ranKpi15.export')}
              </Button>
            </Space>
          </Col>
        </Row>
      </CardBase>

      {err && (
        <Alert
          type="error"
          showIcon
          message={t('ranKpi15.loadFail')}
          description={<Space direction="vertical" size={2}><Text>{err}</Text><Text type="secondary" style={{ fontSize: 12 }}>{t('ranKpi15.hint')}</Text></Space>}
          style={{ marginBottom: 12 }}
        />
      )}

      {/* 口径说明：默认收起（AGENTS.md 三.3b）。讲「怎么算的」；算错/算不全的异常才常驻 */}
      <CalcChainNote
        title={t('ranKpi15.note.title')}
        style={{ marginBottom: 12 }}
        sections={[
          { title: t('ranKpi15.note.sec.source'), lines: [t('ranKpi15.note.source'), t('ranKpi15.note.templates')] },
          { title: t('ranKpi15.note.sec.window'), lines: [t('ranKpi15.note.window'), t('ranKpi15.note.day7')] },
          { title: t('ranKpi15.note.sec.pct'), lines: [t('ranKpi15.note.pct'), t('ranKpi15.note.scope')] },
        ]}
      />

      <Tabs
        activeKey={tech}
        onChange={(k) => setTech(k as Tech)}
        items={TECHS.map((x) => ({ key: x, label: `${x}` }))}
        style={{ marginBottom: 0 }}
      />

      {loading && !data ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      ) : (
        <Row gutter={[12, 12]}>
          {widgets.map((w) => (
            <Col key={w.id} xs={24} md={12} xl={8}>
              <WidgetCard w={w} height={chartHeight} />
            </Col>
          ))}
        </Row>
      )}
    </div>
  )
}
