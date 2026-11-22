/* ============================================================================
 * CS / PS CN KPI 15 分钟看板（?page=cs_cn_kpi_15mins / ?page=ps_cn_kpi_15mins）
 * ----------------------------------------------------------------------------
 * 数据源：后端 /api/cn-kpi-15mins/{cs|ps}/overview
 *   → services/cn/cn_kpi_15mins_widgets.py（远端 db_ps_cs_cn 只读 SQL API）
 *
 * 与参考页 cs_cn_kpi / ps_cn_kpi 的关系：
 *   · 指标与其 UME 段一一对应（同一批表、同一批列名）；
 *   · **不取 EMS**（用户 2026-11-22 明确要求），整页只有 UME；
 *   · 参考页按可选日期区间画日/小时点，本页固定「D0 今天 / D1 昨天 / D7 7 天前」
 *     三条曲线，横轴为 15 分钟桶（00:00/00:15/…），用来对比同一时刻。
 *   · 不同卡片的**源粒度不同**（15 分钟 / 5 分钟聚合 / 只有小时档），
 *     卡头用角标写明；小时源卡片只在整点有数据点，**不做前向填充**。
 *
 * 约定：卡片走 components/cards，导出走 utils/excelReport，口径说明默认收起
 * （CalcChainNote），卡头 ⓘ 说明数据表/粒度/SQL（AGENTS.md 三.3b / 三.3c）。
 * ========================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Col, Empty, Row, Space, Spin, Tag, Typography } from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import ReactECharts from '../../components/ECharts'
import { CardBase } from '../../components/cards'
import CalcChainNote from '../../components/CalcChainNote'
import { ExplainLabel } from '../../components/MetricExplain'
import { exportReport, type ReportSheetSpec } from '../../utils/excelReport'
import { getCnKpi15minsOverview } from '../../api'
import { useI18n } from '../../i18n'
import { formatDateTime } from '../../datetime'
import { useTheme } from '../../theme'
import {
  buildOption15, emptyReason15, grainKey, seriesName, widgetExplainText,
  type Overview15, type Widget15,
} from './CnKpi15mHelpers'

const { Text } = Typography

/** 单个图表卡：图表 / 空态 / 错误三种状态 */
function WidgetCard({ w, height }: { w: Widget15; height: number }) {
  const { isDark } = useTheme()
  const { t } = useI18n()
  const option = useMemo(
    () => (w.empty || w.error || w.rows.length === 0 ? null : buildOption15(w, isDark)),
    [w, isDark],
  )
  const gk = grainKey(w.grain)
  return (
    <CardBase
      size="small"
      style={{ height, display: 'flex', flexDirection: 'column' }}
      bodyStyle={{ flex: 1, minHeight: 0, padding: 8, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
      title={(
        <ExplainLabel
          label={<span style={{ fontSize: 13 }}>{w.title}</span>}
          text={[widgetExplainText(w, t), t('cnkpi15.explain.common')].filter(Boolean).join('\n')}
        />
      )}
      extra={(
        <Space size={4}>
          {gk ? <Tag color={w.grain === '1h' ? 'purple' : w.grain === '5m' ? 'gold' : 'blue'} style={{ marginInlineEnd: 0, fontSize: 11 }}>{t(gk)}</Tag> : null}
          {w.pct ? <Tag color="cyan" style={{ marginInlineEnd: 0 }}>%</Tag> : null}
        </Space>
      )}
    >
      {w.error ? (
        <Alert type="error" showIcon message={t('cnkpiSplit.queryFail')} description={w.error} style={{ fontSize: 12 }} />
      ) : w.empty || !option ? (
        <div style={{ margin: 'auto', maxWidth: 320, textAlign: 'center' }}>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<span style={{ fontSize: 12 }}>{emptyReason15(w, t)}</span>}
          />
        </div>
      ) : (
        <>
          {/* 一张卡只有一个指标：三个色块由 ECharts 图例给出（D0/D1/D7），
              这里只补源粒度，避免把「小时源」当成缺数。 */}
          <div style={{ fontSize: 11, padding: '0 2px 3px' }}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              {t(w.grain === '1h' ? 'cnkpi15.axisHint.hour' : w.grain === '5m' ? 'cnkpi15.axisHint.5m' : 'cnkpi15.axisHint.15m')}
            </Text>
          </div>
          <div style={{ flex: '1 1 auto', minHeight: 0 }}>
            <ReactECharts option={option} notMerge style={{ width: '100%', height: '100%' }} opts={{ renderer: 'canvas' }} />
          </div>
        </>
      )}
    </CardBase>
  )
}

export default function CnKpi15mBoard({ board }: { board: 'cs' | 'ps' }) {
  const { t } = useI18n()
  const [data, setData] = useState<Overview15 | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  // 只认最后一次请求（连续点刷新 / 自动刷新与手动刷新撞车时，先发的响应不许盖掉后发的）
  const reqSeq = useRef(0)
  const inflight = useRef(false)
  const dataRef = useRef<Overview15 | null>(null)

  /** silent=true 供顶栏自动刷新：不 setLoading、失败保留上一帧 */
  const load = useCallback(async (opts?: { silent?: boolean; force?: boolean }) => {
    const silent = !!opts?.silent
    if (silent && !dataRef.current && inflight.current) return
    const seq = ++reqSeq.current
    inflight.current = true
    if (!silent) { setLoading(true); setErr('') }
    try {
      const res = await getCnKpi15minsOverview(board, !!opts?.force)
      if (seq !== reqSeq.current) return
      dataRef.current = res.data as Overview15
      setData(res.data as Overview15)
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

  const widgets = data?.widgets ?? []
  const total = widgets.length
  const withData = widgets.filter((w) => !w.empty && !w.error).length
  const emptyCount = widgets.filter((w) => w.empty).length
  const chartHeight = 340

  /**
   * 小时源卡片的滞后（当前时间 − 这些卡片 D0 最新点）。
   * 这些表由 cn_hourly_kpi_sync 每天 06:20 才同步一次（gr=3600），不是 15 分钟级实时；
   * 到下午看页时 D0 会「只到早上 6 点」，这是采集时延而不是缺数 —— 按 AGENTS
   * 「异常常驻」的约定，滞后超过 3 小时要在页头常驻提示，不能只藏在收起的口径里。
   */
  const staleHourly = (() => {
    if (!data) return null
    const latest: string[] = []
    for (const w of widgets) {
      if (w.grain !== '1h' || w.empty || w.error) continue
      const t0 = w.rows.filter((r) => String(r[2]) === 'D0').map((r) => String(r[0]))
      if (t0.length) latest.push(t0.sort()[t0.length - 1])
    }
    if (!latest.length) return null
    const minT = latest.sort()[0]
    const maxT = latest[latest.length - 1]
    // 库里时间是 UTC，D0 日期也按 UTC 取；不要用浏览器本地时区解析
    const endMs = Date.parse(`${data.meta.window.d0}T${maxT}:00Z`)
    if (!Number.isFinite(endMs)) return null
    const lagH = (Date.now() - endMs) / 3600000
    // 每小时 :05 增量 + 上游约 1 小时时延 → 正常滞后 1~2 小时；超过 3 小时才算异常
    if (lagH < 3) return null
    return {
      minT, maxT,
      now: new Date().toISOString().slice(11, 16),
      lagH: Math.round(lagH * 10) / 10,
      cards: latest.length,
    }
  })()

  /** 导出 Excel：总览 + 每个组件一张宽表（行=时刻，列=维度·日子） */
  const exportExcel = useCallback(() => {
    if (!data) return
    const stamp = dayjs().format('YYYYMMDD_HHmmss')
    const win = data.meta.window
    const sheets: ReportSheetSpec[] = [{
      name: 'Overview',
      title: data.title,
      subtitle: `${t('cnkpi15.windowLabel')}: D0 ${win.d0} · D1 ${win.d1} · D7 ${win.d7}`,
      header: ['', ''],
      rows: [
        [t('cnkpi15.exportSource'), `${data.meta.db} @ ${data.meta.host}`],
        [t('cnkpi15.windowLabel'), `D0 ${win.d0} / D1 ${win.d1} / D7 ${win.d7}`],
        [t('cnkpi15.exportGenerated'), formatDateTime(new Date(), { zone: true })],
        [],
        ...widgets.map((w) => [
          w.title,
          w.empty ? emptyReason15(w, t) : `${w.rows.length} ${t('cnkpiSplit.rows')} · ${grainKey(w.grain) ? t(grainKey(w.grain)) : ''}`,
        ]),
      ] as (string | number)[][],
      columnWidths: [32, 70],
    }]

    for (const w of widgets) {
      if (w.empty || w.error || w.rows.length === 0) continue
      // [t, dim, day, v] → 宽表
      const times: string[] = []
      const seen = new Set<string>()
      const dims: string[] = []
      const days: string[] = []
      const map = new Map<string, number | null>()
      for (const r of w.rows) {
        const tv = String(r[0] ?? '')
        const dim = String(r[1] ?? '')
        const day = String(r[2] ?? '')
        if (!tv || !day) continue
        if (!seen.has(tv)) { seen.add(tv); times.push(tv) }
        if (!dims.includes(dim)) dims.push(dim)
        if (!days.includes(day)) days.push(day)
        const n = r[3] === null || r[3] === undefined || r[3] === '' ? null : Number(r[3])
        map.set(`${tv}\u0000${dim}\u0000${day}`, Number.isFinite(n as number) ? n : null)
      }
      times.sort()
      const dayOrder = ['D0', 'D1', 'D7'].filter((d) => days.includes(d))
      const dimOrder = w.dims.filter((d) => dims.includes(d))
      for (const d of dims) if (!dimOrder.includes(d)) dimOrder.push(d)
      const cols = dimOrder.flatMap((dim) => dayOrder.map((day) => seriesName(w, dim, day)))
      const header = [t('cnkpiSplit.time'), ...cols]
      const body = times.map((tv) => [
        tv,
        ...dimOrder.flatMap((dim) => dayOrder.map((day) => map.get(`${tv}\u0000${dim}\u0000${day}`) ?? null)),
      ])
      sheets.push({
        name: w.id,
        title: w.title,
        subtitle: `${t('cnkpi15.exportSource')}: ${w.source_table}`,
        header,
        rows: body as (string | number | null)[][],
        columnWidths: [12, ...cols.map(() => 18)],
      })
    }
    void exportReport(sheets, `${board.toUpperCase()}_CN_KPI_15MINS_${stamp}.xlsx`)
  }, [data, widgets, board, t])

  return (
    <div style={{ padding: 4 }}>
      {/* 工具条：全局 Header 已有页面标题，页内只放窗口 / 统计 / 操作 */}
      <CardBase size="small" style={{ marginBottom: 12 }} bodyStyle={{ padding: '8px 12px' }}>
        <Row align="middle" justify="space-between" gutter={[12, 8]}>
          <Col>
            <Space size={10} wrap>
              <Tag color="blue" style={{ marginInlineEnd: 0 }}>{t('cnkpi15.onlyUme')}</Tag>
              {data?.meta && (
                <Space size={4} wrap>
                  <Tag color="green">D0 {data.meta.window.d0}</Tag>
                  <Tag color="geekblue">D1 {data.meta.window.d1}</Tag>
                  <Tag color="cyan">D7 {data.meta.window.d7}</Tag>
                  <Tag style={{ marginInlineEnd: 0 }}>{t('cnkpi15.dayLine')}</Tag>
                </Space>
              )}
              {data?.meta && <Tag>{t('cnkpiSplit.summary', { ok: withData, total })}</Tag>}
              {data?.meta && data.meta.failed > 0 && (
                <Tag color="error">{t('cnkpiSplit.failed', { n: data.meta.failed })}</Tag>
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

      {/* 数据滞后是「异常」不是「口径」：常驻可见（AGENTS.md 三.3b）。
          小时源每天 06:20 才同步一次，下午看页时 D0 只到早上 6 点，必须说清。 */}
      {staleHourly && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={staleHourly.minT === staleHourly.maxT
            ? t('cnkpi15.stale.msgOne', {
                time: staleHourly.maxT, now: staleHourly.now, h: staleHourly.lagH,
              })
            : t('cnkpi15.stale.msg', {
                from: staleHourly.minT, to: staleHourly.maxT,
                now: staleHourly.now, h: staleHourly.lagH, n: staleHourly.cards,
              })}
          description={t('cnkpi15.stale.why')}
        />
      )}

      {/* 整页口径：默认收起（AGENTS.md 三.3b）；空态/失败是「状态」，仍常驻在卡片里 */}
      <div style={{ marginBottom: 12 }}>
        <CalcChainNote
          title={t('cnkpi15.note.title')}
          sections={[
            { title: t('cnkpi15.note.secSource'), lines: [t('cnkpi15.note.source1'), t('cnkpi15.note.source2')] },
            { title: t('cnkpi15.note.secWindow'), lines: [t('cnkpi15.note.window1'), t('cnkpi15.note.window2'), t('cnkpi15.note.window3')] },
            {
              title: t('cnkpi15.note.secGran'),
              // CS 页没有 PS 专属的 15 分钟原生卡/吞吐卡，说明按看板分开写，
              // 免得用户在 CS 页看到一堆本页不存在的组件名。
              lines: board === 'ps'
                ? [t('cnkpi15.note.gran1'), t('cnkpi15.note.gran2'), t('cnkpi15.note.gran3')]
                : [t('cnkpi15.note.granCs1'), t('cnkpi15.note.granCs2')],
            },
            { title: t('cnkpi15.note.secAgg'), lines: [t('cnkpi15.note.agg1'), t('cnkpi15.note.agg2'), t('cnkpi15.note.agg3')] },
            ...(board === 'ps'
              ? [{ title: t('cnkpi15.note.secMissing'), lines: [t('cnkpi15.note.missing1'), t('cnkpi15.note.missing2')] }]
              : []),
          ]}
        />
      </div>

      {loading && !data ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      ) : (
        <>
          <Row gutter={[12, 12]}>
            {widgets.map((w) => (
              <Col key={w.id} xs={24} md={12} xl={8}>
                <WidgetCard w={w} height={chartHeight} />
              </Col>
            ))}
          </Row>
          {data && total > 0 && emptyCount === total && (
            <Alert
              type="warning"
              showIcon
              message={t('cnkpi15.allEmpty')}
              style={{ marginTop: 8 }}
            />
          )}
        </>
      )}
    </div>
  )
}
