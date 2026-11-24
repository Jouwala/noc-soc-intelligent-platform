/* ============================================================================
 * PRTG SLA 与容量视图（?page=prtg_links 的「SLA 与容量」页签，Issue #231）
 * ----------------------------------------------------------------------------
 * 数据源：GET /api/prtg/sla?month=YYYY-MM&target=99.5、GET /api/prtg/capacity
 *
 * 口径（与 backend/services/prtg/sla.py 的 method 字段一致）：
 *   - 分母是 PRTG 监控覆盖时间，Paused/维护窗口不计入；
 *   - 月故障时长 = 相邻日快照累计 downtime 的差分；首日无基线时回退累计可用率；
 *   - 利用率只在有容量时算（max(In/Cin, Out/Cout)）；无容量显示「待补全」；
 *   - 取不到显示「—」，不显示 0。
 * ========================================================================== */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, DatePicker, Empty, Segmented, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { type Dayjs } from 'dayjs'

import { getPrtgCapacity, getPrtgSla } from '../../api'
import { useI18n } from '../../i18n'
import { useTheme, WH } from '../../theme'
import { CardBase, StatCard } from '../../components/cards'
import CalcChainNote from '../../components/CalcChainNote'
import { ExplainIcon } from '../../components/MetricExplain'
import {
  fmtInt, fmtNum, fmtTime, NA,
  PrtgCapacityRow, PrtgSlaLink, PrtgSlaReport,
} from './prtgModels'

const { Text } = Typography

const TARGET = 99.5

export default function PrtgSlaView() {
  const { t } = useI18n()
  const { isDark } = useTheme()
  const [month, setMonth] = useState<Dayjs>(dayjs())
  const [report, setReport] = useState<PrtgSlaReport | null>(null)
  const [capacity, setCapacity] = useState<PrtgCapacityRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [slaRes, capRes] = await Promise.all([
        getPrtgSla({ month: month.format('YYYY-MM'), target: TARGET }),
        getPrtgCapacity(),
      ])
      setReport(slaRes.data || null)
      setCapacity(Array.isArray(capRes.data?.capacity) ? capRes.data.capacity : [])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
      setReport(null)
      setCapacity([])
    } finally {
      setLoading(false)
    }
  }, [month])

  useEffect(() => { void load() }, [load])

  const summary = report?.summary
  const kpi = useMemo(() => ([
    {
      key: 'availability',
      title: t('prtg.sla.kpi.availability'),
      value: summary?.avg_availability_pct == null ? NA : fmtNum(summary.avg_availability_pct, 3),
      suffix: summary?.avg_availability_pct == null ? '' : '%',
      explain: t('prtg.sla.explain.availability'),
      color: WH.teal,
    },
    {
      key: 'below',
      title: t('prtg.sla.kpi.below'),
      value: summary ? fmtInt(summary.below_target) : NA,
      explain: t('prtg.sla.explain.below', { target: TARGET }),
      color: (summary?.below_target ?? 0) > 0 ? WH.danger : WH.ok,
    },
    {
      key: 'downtime',
      title: t('prtg.sla.kpi.downtime'),
      value: summary?.total_downtime_hours_month == null ? NA : fmtNum(summary.total_downtime_hours_month, 2),
      suffix: summary?.total_downtime_hours_month == null ? '' : 'h',
      explain: t('prtg.sla.explain.downtime'),
      color: WH.warn,
    },
    {
      key: 'capacity',
      title: t('prtg.sla.kpi.capacity'),
      value: summary ? `${summary.capacity_known} / ${summary.capacity_total}` : NA,
      explain: t('prtg.sla.explain.capacity'),
      color: (summary?.capacity_known ?? 0) > 0 ? WH.teal : WH.warn,
    },
  ]), [summary, t])

  const linkColumns = useMemo<ColumnsType<PrtgSlaLink>>(() => [
    {
      title: <>{t('prtg.sla.col.link')} <ExplainIcon text={t('prtg.sla.colExplain.link')} /></>,
      key: 'link',
      width: 250,
      render: (_: unknown, row: PrtgSlaLink) => (
        <div>
          <div style={{ color: WH.text }}>{row.device_name || NA}</div>
          <div style={{ fontSize: 12, color: WH.textMuted }}>{row.sensor_id} · {row.sensor_name}</div>
        </div>
      ),
    },
    {
      title: t('prtg.sla.col.site'),
      key: 'site',
      width: 140,
      render: (_: unknown, row: PrtgSlaLink) => `${row.site_id || NA} · ${row.region || NA}`,
    },
    {
      title: <>{t('prtg.sla.col.availability')} <ExplainIcon text={t('prtg.sla.colExplain.availability')} /></>,
      key: 'availability',
      width: 130,
      align: 'right',
      render: (_: unknown, row: PrtgSlaLink) => (
        <span style={{ color: row.below_target ? WH.danger : undefined, fontVariantNumeric: 'tabular-nums' }}>
          {row.availability_pct == null ? NA : `${fmtNum(row.availability_pct, 3)}%`}
        </span>
      ),
    },
    {
      title: t('prtg.sla.col.uptimeAll'),
      key: 'uptimeAll',
      width: 125,
      align: 'right',
      render: (_: unknown, row: PrtgSlaLink) => (
        row.uptime_pct_all_time == null ? NA : `${fmtNum(row.uptime_pct_all_time, 3)}%`
      ),
    },
    {
      title: t('prtg.sla.col.downtime'),
      key: 'downtime',
      width: 115,
      align: 'right',
      render: (_: unknown, row: PrtgSlaLink) => (
        row.downtime_hours_month == null ? NA : `${fmtNum(row.downtime_hours_month, 2)} h`
      ),
    },
    {
      title: t('prtg.sla.col.downCount'),
      key: 'downCount',
      width: 95,
      align: 'right',
      render: (_: unknown, row: PrtgSlaLink) => fmtInt(row.down_count ?? null),
    },
    {
      title: t('prtg.sla.col.lastDown'),
      dataIndex: 'last_down',
      width: 165,
      render: (v: string | null | undefined) => fmtTime(v),
    },
    {
      title: t('prtg.sla.col.basis'),
      key: 'basis',
      width: 135,
      render: (_: unknown, row: PrtgSlaLink) => (
        <Tag color={row.basis === 'month_delta' ? 'green' : row.basis === 'undefined_coverage' ? 'red' : 'orange'}>
          {row.basis === 'month_delta'
            ? t('prtg.sla.basis.month')
            : row.basis === 'undefined_coverage'
              ? t('prtg.sla.basis.undefined')
              : t('prtg.sla.basis.allTime')}
        </Tag>
      ),
    },
  ], [t])

  const capacityColumns = useMemo<ColumnsType<PrtgCapacityRow>>(() => [
    {
      title: t('prtg.sla.cap.link'),
      key: 'link',
      width: 250,
      render: (_: unknown, row: PrtgCapacityRow) => (
        <div>
          <div>{row.device_name || NA}</div>
          <div style={{ fontSize: 12, color: WH.textMuted }}>{row.sensor_id} · {row.sensor_name}</div>
        </div>
      ),
    },
    {
      title: t('prtg.sla.cap.capacity'),
      key: 'capacity',
      width: 130,
      align: 'right',
      render: (_: unknown, row: PrtgCapacityRow) => (
        row.capacity_known ? `${fmtNum(row.capacity_mbps, 1)} Mbps` : t('prtg.capacityMissing')
      ),
    },
    {
      title: t('prtg.sla.cap.peak24'),
      key: 'peak24',
      width: 160,
      align: 'right',
      render: (_: unknown, row: PrtgCapacityRow) => (
        `${row.peak_24h_in_mbps == null ? NA : fmtNum(row.peak_24h_in_mbps)} / ${row.peak_24h_out_mbps == null ? NA : fmtNum(row.peak_24h_out_mbps)}`
      ),
    },
    {
      title: t('prtg.sla.cap.peak7d'),
      key: 'peak7d',
      width: 160,
      align: 'right',
      render: (_: unknown, row: PrtgCapacityRow) => (
        `${row.peak_7d_in_mbps == null ? NA : fmtNum(row.peak_7d_in_mbps)} / ${row.peak_7d_out_mbps == null ? NA : fmtNum(row.peak_7d_out_mbps)}`
      ),
    },
    {
      title: t('prtg.sla.cap.p95'),
      key: 'p95',
      width: 160,
      align: 'right',
      render: (_: unknown, row: PrtgCapacityRow) => (
        `${row.p95_30d_in_mbps == null ? NA : fmtNum(row.p95_30d_in_mbps)} / ${row.p95_30d_out_mbps == null ? NA : fmtNum(row.p95_30d_out_mbps)}`
      ),
    },
    {
      title: t('prtg.sla.cap.utilization'),
      key: 'utilization',
      width: 120,
      align: 'right',
      render: (_: unknown, row: PrtgCapacityRow) => {
        if (row.utilization_pct == null) return NA
        const color = row.level === 'critical' ? WH.danger : row.level === 'warning' ? WH.warn : undefined
        return <span style={{ color }}>{fmtNum(row.utilization_pct, 2)}%</span>
      },
    },
    {
      title: t('prtg.sla.cap.level'),
      key: 'level',
      width: 120,
      render: (_: unknown, row: PrtgCapacityRow) => (
        <Tag color={row.level === 'critical' ? 'red' : row.level === 'warning' ? 'orange' : row.level === 'attention' ? 'gold' : row.level === 'ok' ? 'green' : 'default'}>
          {row.level === 'critical' ? t('prtg.sla.level.critical')
            : row.level === 'warning' ? t('prtg.sla.level.warning')
              : row.level === 'attention' ? t('prtg.sla.level.attention')
                : row.level === 'ok' ? t('prtg.sla.level.ok')
                  : t('prtg.sla.level.unknown')}
        </Tag>
      ),
    },
  ], [t])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>
      <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
        <Space wrap>
          <Text type="secondary">{t('prtg.sla.month')}</Text>
          <DatePicker
            picker="month"
            value={month}
            allowClear={false}
            onChange={(v) => v && setMonth(v)}
            disabled={loading}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('prtg.sla.target', { target: TARGET })}
          </Text>
          <Tag>{t('prtg.sla.rows', { n: summary?.links ?? NA, d: summary?.sla_days_recorded ?? NA })}</Tag>
        </Space>
      </CardBase>

      {error && <Alert type="error" showIcon message={t('prtg.sla.error')} description={error} />}

      <CalcChainNote
        title={t('prtg.sla.note.title')}
        sections={[
          { title: t('prtg.sla.note.denominatorTitle'), lines: [t('prtg.sla.note.denominator')] },
          { title: t('prtg.sla.note.downtimeTitle'), lines: [t('prtg.sla.note.downtime')] },
          { title: t('prtg.sla.note.capacityTitle'), lines: [t('prtg.sla.note.capacity')] },
        ]}
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
        {kpi.map(card => (
          <StatCard
            key={card.key}
            title={(
              <span>
                {card.title}
                <span style={{ marginLeft: 4 }}><ExplainIcon text={card.explain} /></span>
              </span>
            )}
            value={card.value}
            suffix={card.suffix}
            explainText={card.explain}
            accentColor={card.color}
          />
        ))}
      </div>

      <CardBase
        title={<>{t('prtg.sla.links.title')} <ExplainIcon text={t('prtg.sla.links.hint')} /></>}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>{report?.month || ''}</Text>}
      >
        <Table<PrtgSlaLink>
          rowKey="sensor_id"
          columns={linkColumns}
          dataSource={report?.links || []}
          loading={loading}
          pagination={false}
          size="small"
          scroll={{ x: 1350 }}
          locale={{ emptyText: <Empty description={t('prtg.sla.empty')} /> }}
        />
      </CardBase>

      <CardBase
        title={<>{t('prtg.sla.region.title')} <ExplainIcon text={t('prtg.sla.region.hint')} /></>}
      >
        <Table
          rowKey="region"
          size="small"
          pagination={false}
          dataSource={report?.region_ranking || []}
          columns={[
            { title: t('prtg.sla.region.region'), dataIndex: 'region', key: 'region' },
            { title: t('prtg.sla.region.links'), dataIndex: 'links', key: 'links', align: 'right' },
            {
              title: t('prtg.sla.region.avg'), dataIndex: 'avg_availability_pct', key: 'avg', align: 'right',
              render: (v: number) => (v == null ? NA : `${fmtNum(v, 3)}%`),
            },
            { title: t('prtg.sla.region.below'), dataIndex: 'below_target', key: 'below', align: 'right' },
          ]}
        />
      </CardBase>

      <CardBase
        title={<>{t('prtg.sla.capacity.title')} <ExplainIcon text={t('prtg.sla.capacity.hint')} /></>}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>{t('prtg.sla.capacity.source')}</Text>}
      >
        <Table<PrtgCapacityRow>
          rowKey="sensor_id"
          columns={capacityColumns}
          dataSource={capacity}
          loading={loading}
          pagination={false}
          size="small"
          scroll={{ x: 1250 }}
          locale={{ emptyText: <Empty description={t('prtg.sla.empty')} /> }}
        />
      </CardBase>
    </div>
  )
}
