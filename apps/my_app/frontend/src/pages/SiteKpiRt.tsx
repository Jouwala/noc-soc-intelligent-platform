import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Col, Empty, Input, Progress, Row, Segmented, Select, Space, Tag, Typography } from 'antd'
import { ClearOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import ReactECharts from '../components/ECharts'
import { CardBase } from '../components/cards'
import { useI18n } from '../i18n'
import { useTheme, darkTooltip, axisLabel, axisLine } from '../theme'
import { siteKpiRtSites, siteKpiRtQuery, siteKpiRtJob, siteKpiRtCacheClear } from '../api'

const { Text } = Typography

interface SiteRow {
  enodeb_id: string
  name: string
  cells?: number
  sectors?: number
}

interface CellMeta {
  cell_name: string
  cell_id?: string | null
  sector: number
  carrier_mhz?: number | null
  carrier_label: string
  source: string
}

interface MetricMeta {
  key: string
  label: string
  unit: string
}

interface SiteKpiResult {
  site: { enodeb_id: string; name: string }
  tech?: string
  granularity_note?: string
  range: { start: string; end: string; days: number; gran: string }
  generated_at: string
  elapsed_ms: number
  row_count: number
  grid: string[]
  cells: CellMeta[]
  metrics: MetricMeta[]
  series: Record<string, Record<string, [string, number | null][]>>
  warnings: string[]
}

/** 指标文案键：显式列举，避免动态拼键被 check:i18n 判为缺失。 */
function metricLabelKey(key: string) {
  switch (key) {
    case 'dl_prb': return 'siteKpiRt.m.dlPrb'
    case 'ul_prb': return 'siteKpiRt.m.ulPrb'
    case 'active_users': return 'siteKpiRt.m.users'
    case 'active_ues': return 'siteKpiRt.m.activeUes'
    case 'dl_traffic_gb': return 'siteKpiRt.m.dlTraffic'
    case 'ul_traffic_gb': return 'siteKpiRt.m.ulTraffic'
    case 'dl_thr_mbps': return 'siteKpiRt.m.dlThr'
    case 'ul_thr_mbps': return 'siteKpiRt.m.ulThr'
    case 'dl_user_thr_mbps': return 'siteKpiRt.m.dlUserThr'
    case 'ul_user_thr_mbps': return 'siteKpiRt.m.ulUserThr'
    default: return ''
  }
}

const DEFAULT_METRICS = ['dl_prb', 'active_users', 'dl_traffic_gb', 'dl_user_thr_mbps']

/** 同扇区多载波：一条曲线一个小区，颜色按频点固定（同一站点内稳定，切换指标不变）。 */
function carrierColorMap(cells: CellMeta[], colors: string[]) {
  const uniq = Array.from(new Set(cells.map(c => c.carrier_label))).sort()
  const map = new Map<string, string>()
  uniq.forEach((label, i) => map.set(label, colors[i % colors.length]))
  return map
}

function buildOption(
  metric: MetricMeta,
  cells: CellMeta[],
  result: SiteKpiResult,
  isDark: boolean,
  colorOf: (label: string) => string,
) {
  const cats = result.grid.map(g => g.slice(5, 16))
  const dense = cats.length > 240
  const series = cells.map(c => {
    const raw = result.series[c.cell_name]?.[metric.key] || []
    const lookup = new Map(raw.map(p => [p[0], p[1]]))
    return {
      name: `${c.cell_name}${c.carrier_mhz != null ? ` · ${c.carrier_label}` : ''}`,
      type: 'line' as const,
      smooth: false,
      showSymbol: false,
      connectNulls: true,
      sampling: dense ? ('lttb' as const) : undefined,
      lineStyle: { width: 1.6, color: colorOf(c.carrier_label) },
      itemStyle: { color: colorOf(c.carrier_label) },
      emphasis: { focus: 'series' as const },
      data: result.grid.map(g => {
        const v = lookup.get(g)
        return v === undefined ? null : v
      }),
    }
  })
  const unit = metric.unit
  return {
    backgroundColor: 'transparent',
    tooltip: {
      ...darkTooltip(isDark),
      trigger: 'axis' as const,
      valueFormatter: (v: unknown) =>
        v === null || v === undefined || v === '' ? '—' : `${Number(v).toFixed(2)}${unit ? ` ${unit}` : ''}`,
    },
    legend: {
      top: 0,
      type: 'scroll' as const,
      icon: 'roundRect',
      itemWidth: 14,
      itemHeight: 8,
      textStyle: { ...axisLabel(isDark), fontSize: 10 },
    },
    grid: { left: 10, right: 14, top: 30, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: cats,
      boundaryGap: false,
      axisLabel: { ...axisLabel(isDark), hideOverlap: true, fontSize: 10 },
      axisLine: axisLine(isDark),
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value' as const,
      scale: true,
      splitLine: { show: false },
      axisLabel: { ...axisLabel(isDark), fontSize: 10 },
    },
    series,
  }
}

export default function SiteKpiRt() {
  const { t } = useI18n()
  const { isDark, colors } = useTheme()

  const [tech, setTech] = useState<'4G' | '5G'>('4G')
  const [keyword, setKeyword] = useState('')
  const [candidates, setCandidates] = useState<SiteRow[]>([])
  const [site, setSite] = useState('')
  const [days, setDays] = useState(8)
  const [gran, setGran] = useState<'auto' | '15min'>('auto')

  const [jobId, setJobId] = useState('')
  const [status, setStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [progress, setProgress] = useState(0)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const [result, setResult] = useState<SiteKpiResult | null>(null)
  const [picked, setPicked] = useState<string[]>(DEFAULT_METRICS)

  const timer = useRef<number | null>(null)

  const stopPolling = useCallback(() => {
    if (timer.current) {
      window.clearInterval(timer.current)
      timer.current = null
    }
  }, [])

  useEffect(() => () => stopPolling(), [stopPolling])

  const start = useCallback(async (siteId: string, force = false) => {
    if (!siteId) return
    setError('')
    setStatus('running')
    setProgress(0)
    setMessage('')
    setResult(null)
    try {
      const r = await siteKpiRtQuery({ site: siteId, days, gran, force, tech })
      const jid = r.data?.job_id
      if (!jid) throw new Error(t('siteKpiRt.badResponse'))
      setJobId(jid)
    } catch (e: any) {
      setStatus('error')
      setError(e?.response?.data?.detail || e?.message || String(e))
    }
  }, [days, gran, tech, t])

  // 轮询任务；一次 8 天窗口要按天切片取数，通常 1~5 分钟
  useEffect(() => {
    stopPolling()
    if (!jobId || status !== 'running') return
    const tick = async () => {
      try {
        const r = await siteKpiRtJob(jobId)
        const d = r.data || {}
        setProgress(Number(d.progress) || 0)
        setMessage(String(d.message || ''))
        if (d.status === 'done') {
          setStatus('done')
          setResult(d.result || null)
          stopPolling()
        } else if (d.status === 'error') {
          setStatus('error')
          setError(String(d.message || t('siteKpiRt.failed')))
          stopPolling()
        }
      } catch (e: any) {
        setStatus('error')
        setError(e?.response?.data?.detail || e?.message || String(e))
        stopPolling()
      }
    }
    void tick()
    timer.current = window.setInterval(tick, 3000)
    return stopPolling
  }, [jobId, status, stopPolling, t])

  const search = useCallback(async () => {
    const kw = keyword.trim()
    if (!kw) return
    setError('')
    try {
      const r = await siteKpiRtSites({ q: kw, limit: 30, tech })
      const list: SiteRow[] = r.data?.sites || []
      setCandidates(list)
      if (!list.length) {
        setError(t('siteKpiRt.notFound', { kw }))
        return
      }
      if (list.length === 1) {
        setSite(list[0].enodeb_id)
        void start(list[0].enodeb_id)
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || String(e))
    }
  }, [keyword, tech, start, t])

  const sectors = useMemo(() => {
    if (!result) return []
    const map = new Map<number, CellMeta[]>()
    for (const c of result.cells) {
      const k = Number(c.sector) || 0
      if (!map.has(k)) map.set(k, [])
      map.get(k)!.push(c)
    }
    return Array.from(map.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([sector, cells]) => ({ sector, cells }))
  }, [result])

  const colorOf = useMemo(() => {
    const m = carrierColorMap(result?.cells || [], colors)
    return (label: string) => m.get(label) || colors[0]
  }, [result, colors])

  const shownMetrics = useMemo(() => {
    if (!result) return []
    return result.metrics.filter(m => picked.includes(m.key) && metricLabelKey(m.key))
  }, [result, picked])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>
      <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
        <Space wrap>
          <Segmented
            value={tech}
            onChange={v => {
              setTech(v as '4G' | '5G')
              setCandidates([])
              setSite('')
              setResult(null)
              setStatus('idle')
              setError('')
            }}
            options={[
              { value: '4G', label: t('siteKpiRt.tech4g') },
              { value: '5G', label: t('siteKpiRt.tech5g') },
            ]}
          />
          <Input.Search
            allowClear
            placeholder={t('siteKpiRt.searchPlaceholder')}
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onSearch={() => void search()}
            enterButton={<SearchOutlined />}
            style={{ width: 320 }}
          />
          <Select
            value={site || undefined}
            placeholder={t('siteKpiRt.pickSite')}
            onChange={v => { setSite(v); void start(v) }}
            options={candidates.map(c => ({
              value: c.enodeb_id,
              label: `${c.name} (${c.enodeb_id})${c.cells ? ` · ${c.cells} ${t('siteKpiRt.cellsUnit')}` : ''}`,
            }))}
            style={{ width: 300 }}
            showSearch
            optionFilterProp="label"
            notFoundContent={t('siteKpiRt.searchFirst')}
          />
          <Select
            value={days}
            onChange={setDays}
            options={[1, 2, 3, 7, 8].map(d => ({ value: d, label: t('siteKpiRt.days', { n: d }) }))}
            style={{ width: 110 }}
          />
          <Select
            value={gran}
            onChange={v => setGran(v)}
            options={[
              { value: 'auto', label: t('siteKpiRt.granAuto') },
              { value: '15min', label: t('siteKpiRt.gran15') },
            ]}
            style={{ width: 150 }}
          />
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            loading={status === 'running'}
            disabled={!site}
            onClick={() => void start(site, true)}
          >
            {t('siteKpiRt.refresh')}
          </Button>
          <Button
            icon={<ClearOutlined />}
            onClick={async () => { await siteKpiRtCacheClear(); setResult(null); setStatus('idle') }}
          >
            {t('siteKpiRt.clearCache')}
          </Button>
        </Space>
      </CardBase>

      {error && <Alert type="error" showIcon message={t('siteKpiRt.failed')} description={error} />}

      {status === 'running' && (
        <CardBase>
          <Space direction="vertical" style={{ width: '100%' }} size={6}>
            <Text strong>{t('siteKpiRt.running')}</Text>
            <Progress percent={progress} status="active" />
            <Text type="secondary" style={{ fontSize: 12 }}>{message || t('siteKpiRt.preparing')}</Text>
          </Space>
        </CardBase>
      )}

      {status === 'idle' && !result && (
        <CardBase>
          <Empty description={t('siteKpiRt.empty')} />
        </CardBase>
      )}

      {result && (
        <>
          <CardBase>
            <Space wrap size={12}>
              <Text strong style={{ fontSize: 15 }}>
                {result.site.name} ({result.site.enodeb_id})
              </Text>
              <Tag color={result.tech === '5G' ? 'purple' : 'blue'}>{result.tech || '4G'}</Tag>
              <Tag>{t('siteKpiRt.sectorCount', { n: sectors.length })}</Tag>
              <Tag>{t('siteKpiRt.cellCount', { n: result.cells.length })}</Tag>
              <Tag color="blue">{t('siteKpiRt.rows', { n: result.row_count })}</Tag>
              <Tag color="default">{t('siteKpiRt.elapsed', { s: (result.elapsed_ms / 1000).toFixed(1) })}</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('siteKpiRt.range', { start: result.range.start.replace('T', ' ').slice(0, 16), end: result.range.end.replace('T', ' ').slice(0, 16) })}
              </Text>
            </Space>
            <div style={{ marginTop: 10 }}>
              <Space wrap align="center">
                <Text style={{ fontSize: 12 }}>{t('siteKpiRt.metrics')}</Text>
                <Checkbox.Group
                  value={picked}
                  onChange={v => setPicked(v as string[])}
                  options={result.metrics
                    .filter(m => metricLabelKey(m.key))
                    .map(m => ({ value: m.key, label: t(metricLabelKey(m.key)) }))}
                />
              </Space>
            </div>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
              {t('siteKpiRt.granHint')}
            </Text>
            <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
              {t('siteKpiRt.source')}
            </Text>
          </CardBase>

          {result.warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message={t('siteKpiRt.warnTitle')}
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {result.warnings.map((w, i) => <li key={i} style={{ fontSize: 12 }}>{w}</li>)}
                </ul>
              }
            />
          )}

          {!shownMetrics.length && (
            <CardBase><Empty description={t('siteKpiRt.needMetric')} /></CardBase>
          )}

          {sectors.map(sec => (
            <CardBase
              key={sec.sector}
              title={`${t('siteKpiRt.sector')} ${sec.sector} · ${t('siteKpiRt.cellCount', { n: sec.cells.length })}`}
              extra={
                <Space size={4} wrap>
                  {sec.cells.map(c => (
                    <Tag
                      key={c.cell_name}
                      style={{ borderColor: colorOf(c.carrier_label), color: colorOf(c.carrier_label), background: 'transparent' }}
                    >
                      {c.cell_name} · {c.carrier_label}
                    </Tag>
                  ))}
                </Space>
              }
            >
              <Row gutter={[12, 12]}>
                {shownMetrics.map(m => (
                  <Col key={m.key} xs={24} xl={12} xxl={8}>
                    <div style={{ border: '1px solid ' + (isDark ? '#173158' : '#e6ecf3'), borderRadius: 6, padding: '6px 8px' }}>
                      <Text style={{ fontSize: 12 }}>
                        {t(metricLabelKey(m.key))}{m.unit ? ` (${m.unit})` : ''}
                      </Text>
                      <ReactECharts
                        option={buildOption(m, sec.cells, result, isDark, colorOf)}
                        style={{ height: 220 }}
                        notMerge
                        opts={{ renderer: 'canvas' }}
                      />
                    </div>
                  </Col>
                ))}
              </Row>
            </CardBase>
          ))}
        </>
      )}
    </div>
  )
}
