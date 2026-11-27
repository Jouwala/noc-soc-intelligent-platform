/* ============================================================================
 * PRTG 设备事件卡片（P0-3，2026-11-26）
 * ----------------------------------------------------------------------------
 * 数据：GET /api/prtg/incidents?window=7d（backend/services/prtg/incidents.py）。
 * 同一设备 120 秒内相继 Down 的传感器合并为一条事件；展开行看各传感器 Down/Up 时间线。
 * 口径：未结束事件没有时长（显示「未结束」，不是 0）；时间 UTC；取不到「—」。
 * ========================================================================== */

import { useEffect, useMemo, useState } from 'react'
import { Alert, Empty, Segmented, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import { getPrtgIncidents } from '../../api'
import { useI18n } from '../../i18n'
import { WH } from '../../theme'
import { CardBase } from '../../components/cards'
import { ExplainIcon, ExplainPopover } from '../../components/MetricExplain'
import { fmtInt, fmtTime, NA } from './prtgModels'
import {
  fmtDuration, incidentKindColor, incidentKindLabelKey,
  type PrtgIncident, type PrtgIncidentSensor,
} from './prtgLogModels'

const { Text } = Typography

type IncidentWindow = '24h' | '7d' | '30d'

interface Props {
  /** 父页面每次刷新时递增，触发重新取数 */
  refreshToken: number
}

export default function PrtgIncidents({ refreshToken }: Props) {
  const { t } = useI18n()
  const [win, setWin] = useState<IncidentWindow>('7d')
  const [items, setItems] = useState<PrtgIncident[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    getPrtgIncidents({ window: win })
      .then(res => {
        if (cancelled) return
        setItems(Array.isArray(res.data?.incidents) ? res.data.incidents : [])
        setLoaded(res.data?.has_data !== false)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : String(e))
        setItems([])
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [win, refreshToken])

  const openCount = useMemo(() => items.filter(i => i.is_open).length, [items])

  const columns = useMemo<ColumnsType<PrtgIncident>>(() => [
    {
      title: <>{t('prtg.incidents.col.start')} <ExplainIcon text={t('prtg.incidents.colExplain.start')} /></>,
      dataIndex: 'started_at',
      width: 170,
      render: (v: string | null | undefined) => fmtTime(v),
    },
    {
      title: t('prtg.incidents.col.device'),
      dataIndex: 'device_name',
      width: 260,
      render: (v: string | null | undefined) => v || NA,
    },
    {
      title: <>{t('prtg.incidents.col.kind')} <ExplainIcon text={t('prtg.incidents.colExplain.kind')} /></>,
      dataIndex: 'kind',
      width: 170,
      render: (_: unknown, r: PrtgIncident) => (
        <ExplainPopover text={t('prtg.incidents.cellExplain.kind', {
          icmp: fmtInt(r.evidence?.icmp_timeouts),
          ping: r.evidence?.has_ping_sensor ? t('common.yes') : t('common.no'),
          window: fmtInt(r.evidence?.merge_window_seconds),
          logs: fmtInt(r.evidence?.log_count),
        })}>
          <Tag color={incidentKindColor(r.kind)}>{t(incidentKindLabelKey(r.kind))}</Tag>
        </ExplainPopover>
      ),
    },
    {
      title: <>{t('prtg.incidents.col.sensors')} <ExplainIcon text={t('prtg.incidents.colExplain.sensors')} /></>,
      dataIndex: 'sensor_count',
      width: 110,
      align: 'right',
      render: (v: number | null | undefined) => fmtInt(v),
    },
    {
      title: <>{t('prtg.incidents.col.duration')} <ExplainIcon text={t('prtg.incidents.colExplain.duration')} /></>,
      key: 'duration',
      width: 130,
      align: 'right',
      render: (_: unknown, r: PrtgIncident) => (r.is_open
        ? <Tag color="red">{t('prtg.incidents.open')}</Tag>
        : <span style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtDuration(r.duration_seconds)}</span>),
    },
    {
      title: t('prtg.incidents.col.end'),
      dataIndex: 'ended_at',
      width: 170,
      render: (v: string | null | undefined) => fmtTime(v),
    },
    {
      title: <>{t('prtg.incidents.col.flaps')} <ExplainIcon text={t('prtg.incidents.colExplain.flaps')} /></>,
      key: 'flaps',
      width: 90,
      align: 'right',
      render: (_: unknown, r: PrtgIncident) => fmtInt(r.evidence?.flaps),
    },
  ], [t])

  const sensorColumns = useMemo<ColumnsType<PrtgIncidentSensor>>(() => [
    { title: t('prtg.incidents.sensor'), dataIndex: 'sensor_name', render: (v: string | null | undefined) => v || NA },
    { title: t('prtg.detail.sensorType'), dataIndex: 'sensor_type', width: 160, render: (v: string | null | undefined) => v || NA },
    { title: t('prtg.incidents.sensorDown'), dataIndex: 'down_at', width: 170, render: (v: string | null | undefined) => fmtTime(v) },
    { title: t('prtg.incidents.sensorUp'), dataIndex: 'up_at', width: 170, render: (v: string | null | undefined) => fmtTime(v) },
  ], [t])

  return (
    <CardBase
      title={<>{t('prtg.incidents.title')} <ExplainIcon text={t('prtg.incidents.explain')} /></>}
      extra={(
        <Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('prtg.incidents.count', { n: items.length, open: openCount })}
          </Text>
          <Segmented
            className="wh-seg"
            size="small"
            value={win}
            onChange={v => setWin(v as IncidentWindow)}
            options={[
              { value: '24h', label: t('prtg.window.24h') },
              { value: '7d', label: t('prtg.window.7d') },
              { value: '30d', label: t('prtg.window.30d') },
            ]}
          />
        </Space>
      )}
    >
      {error && <Alert type="error" showIcon message={t('prtg.incidents.loadFailed')} description={error} />}
      {!error && !loading && !loaded && (
        <Alert type="info" showIcon message={t('prtg.incidents.notYet')} />
      )}
      <Table<PrtgIncident>
        rowKey="id"
        size="small"
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={items.length > 20 ? { pageSize: 20, size: 'small' } : false}
        scroll={{ x: 1100 }}
        expandable={{
          expandedRowRender: r => (
            <Table<PrtgIncidentSensor>
              rowKey={(s, i) => `${s.sensor_id ?? 'x'}-${i}`}
              size="small"
              columns={sensorColumns}
              dataSource={r.sensors || []}
              pagination={false}
            />
          ),
          rowExpandable: r => (r.sensors || []).length > 0,
        }}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={error ? t('prtg.incidents.loadFailed') : t('prtg.incidents.empty')}
            />
          ),
        }}
      />
      <div style={{ fontSize: 11, color: WH.textDim, marginTop: 4 }}>{t('prtg.incidents.note')}</div>
    </CardBase>
  )
}
