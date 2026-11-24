/* ============================================================================
 * PRTG 链路详情（点击主表任意一行后，在 DraggableClickModal 里渲染）
 * ----------------------------------------------------------------------------
 * 数据全部来自 GET /api/prtg/links/{sensor_id}：
 *   基础信息（sensor_type / interval / last_up / last_down / last_check / 容量）
 *   mappings[]（该传感器的全部站点映射）
 *   events[]（最近 20 条状态事件）
 *   series{}（通道 -1 / 0 / 1 时序，value 为原始 byte/s）
 *
 * 前端只做「原始 byte/s → Mbps」这一层单位换算（图表标签已写明 ×8÷1e6），
 * 不重新推导任何 KPI；缺值显示「—」。
 * ========================================================================== */

import { Descriptions, Empty, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import { useI18n } from '../../i18n'
import { WH } from '../../theme'
import { PrtgSeriesChart, type SeriesDef } from './PrtgCharts'
import {
  eventLabelKey, fmtNum, fmtTime, matchLabelKey, NA, PrtgWindow,
  statusColor, statusLabelKey, PrtgEvent, PrtgLinkDetailData, PrtgMapping,
} from './prtgModels'

const { Text } = Typography

/** 详情接口的 series.value 是原始 byte/s；图上换算为 Mbps（仅单位换算） */
function toMbps(v: number | null): number | null {
  if (v == null || !Number.isFinite(v)) return null
  return (v * 8) / 1_000_000
}

interface Props {
  detail: PrtgLinkDetailData
  windowKey: PrtgWindow
  isDark: boolean
}

export default function PrtgLinkDetail({ detail, windowKey, isDark }: Props) {
  const { t } = useI18n()

  const mappings = detail.mappings || []
  const events = detail.events || []
  const series = detail.series || {}
  // Ping 等非 Traffic 传感器：channel 0 是 Ping Time（msec），不能套 In/Out/Total
  // 语义与 byte/s→Mbps 换算（2026-11-24 审核实测），详情里只给一句说明。
  const isTraffic = detail.is_traffic !== false

  const mappingColumns: ColumnsType<PrtgMapping> = [
    {
      title: t('prtg.detail.site'),
      dataIndex: 'site_id',
      render: (v: string | null | undefined) => v || NA,
    },
    {
      title: t('prtg.detail.region'),
      dataIndex: 'region',
      render: (v: string | null | undefined) => v || NA,
    },
    {
      title: t('prtg.detail.match'),
      dataIndex: 'match_status',
      render: (v: string | null | undefined) => <Tag>{t(matchLabelKey(v))}</Tag>,
    },
  ]

  const eventColumns: ColumnsType<PrtgEvent> = [
    {
      title: t('prtg.detail.time'),
      dataIndex: 'happened_at',
      width: 170,
      render: (v: string | null | undefined) => fmtTime(v),
    },
    {
      title: t('prtg.detail.event'),
      dataIndex: 'event_type',
      width: 110,
      render: (v: string | null | undefined) => t(eventLabelKey(v)),
    },
    {
      title: t('prtg.detail.fromTo'),
      key: 'from_to',
      width: 190,
      render: (_: unknown, r: PrtgEvent) => {
        const from = r.from_status ? t(statusLabelKey(r.from_status)) : NA
        const to = r.to_status ? t(statusLabelKey(r.to_status)) : NA
        return `${from} → ${to}`
      },
    },
    {
      title: t('prtg.detail.message'),
      dataIndex: 'message',
      render: (v: string | null | undefined) => v || NA,
    },
  ]

  const seriesDefs: SeriesDef[] = isTraffic
    ? [
        { key: '-1', label: t('prtg.detail.seriesTotal'), color: isDark ? '#00d9ff' : '#0d9488' },
        { key: '0', label: t('prtg.detail.seriesIn'), color: isDark ? '#74c0fc' : '#1971c2' },
        { key: '1', label: t('prtg.detail.seriesOut'), color: isDark ? '#ffa94d' : '#e8590c' },
      ].map(s => ({
        ...s,
        points: (series[s.key] || []).map(p => ({ t: p.t, value: toMbps(p.value) })),
      }))
    : []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>
      {/* ① 传感器基础信息 */}
      <Descriptions
        size="small"
        bordered
        column={{ xs: 1, sm: 2, lg: 3 }}
        items={[
          { key: 'id', label: t('prtg.detail.sensorId'), children: detail.sensor_id },
          { key: 'type', label: t('prtg.detail.sensorType'), children: detail.sensor_type || NA },
          {
            key: 'status',
            label: t('prtg.detail.status'),
            children: (
              <Tag color={statusColor(detail.status)}>{t(statusLabelKey(detail.status))}</Tag>
            ),
          },
          {
            key: 'interval',
            label: t('prtg.detail.interval'),
            children: detail.interval_seconds == null ? NA : `${detail.interval_seconds} s`,
          },
          { key: 'lastUp', label: t('prtg.detail.lastUp'), children: fmtTime(detail.last_up) },
          { key: 'lastDown', label: t('prtg.detail.lastDown'), children: fmtTime(detail.last_down) },
          { key: 'lastCheck', label: t('prtg.detail.lastCheck'), children: fmtTime(detail.last_check) },
          {
            key: 'uptime',
            label: t('prtg.col.uptime'),
            children: detail.uptime_pct == null
              ? NA
              : `${fmtNum(detail.uptime_pct, 3)}%`,
          },
          {
            key: 'capacity',
            label: t('prtg.detail.capacity'),
            children: detail.capacity_mbps == null
              ? t('prtg.capacityMissing')
              : `${fmtNum(detail.capacity_mbps)} ${t('prtg.trend.unit')}`,
          },
        ]}
      />

      {/* ② 站点映射（一个传感器可能对应多个站） */}
      <div>
        <Text strong>{t('prtg.detail.mappings')}</Text>
        <Table<PrtgMapping>
          style={{ marginTop: 'var(--wh-space-xs)' }}
          rowKey={r => `${r.site_id || ''}-${r.region || ''}-${r.match_status || ''}`}
          columns={mappingColumns}
          dataSource={mappings}
          size="small"
          pagination={false}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('prtg.detail.noMappings')} /> }}
        />
      </div>

      {/* ③ 最近状态事件 */}
      <div>
        <Text strong>{t('prtg.detail.events')}</Text>
        <Table<PrtgEvent>
          style={{ marginTop: 'var(--wh-space-xs)' }}
          rowKey={(r, i) => `${r.happened_at || ''}-${r.event_type || ''}-${i}`}
          columns={eventColumns}
          dataSource={events}
          size="small"
          pagination={false}
          scroll={{ x: 620 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('prtg.detail.noEvents')} /> }}
        />
      </div>

      {/* ④ In / Out / Total 时序（Ping 等非流量传感器不画，避免错误语义） */}
      <div>
        <Text strong>
          {isTraffic ? t('prtg.detail.series') : t('prtg.detail.seriesPingTitle')}
        </Text>
        <div style={{ marginTop: 'var(--wh-space-xs)' }}>
          {isTraffic ? (
            <PrtgSeriesChart series={seriesDefs} windowKey={windowKey} isDark={isDark} emptyText={t('prtg.detail.noSeries')} />
          ) : (
            <Text type="secondary">{t('prtg.detail.pingNote')}</Text>
          )}
        </div>
        <div style={{ fontSize: 11, color: WH.textDim, marginTop: 4 }}>
          {t('prtg.detail.seriesNote')}
        </div>
      </div>
    </div>
  )
}
