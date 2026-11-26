/* ============================================================================
 * PRTG 日志表（链路详情弹窗里用，P0-1，2026-11-26）
 * ----------------------------------------------------------------------------
 * 数据：GET /api/prtg/logs?sensor_id=…&reason=…&limit=200（backend/services/prtg/logs.py）。
 * reason 由后端按原文分类（ICMP 超时 / SNMP 无响应 / 偏低 / 偏高 …），前端只显示标签。
 * ========================================================================== */

import { useEffect, useMemo, useState } from 'react'
import { Alert, Empty, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import { getPrtgLogs } from '../../api'
import { useI18n } from '../../i18n'
import { fmtTime, NA, statusColor, statusLabelKey } from './prtgModels'
import {
  PRTG_LOG_REASONS, reasonColor, reasonLabelKey, type PrtgLogReason, type PrtgLogRow,
} from './prtgLogModels'

const { Text } = Typography

interface Props {
  sensorId: number
}

export default function PrtgLogTable({ sensorId }: Props) {
  const { t } = useI18n()
  const [reason, setReason] = useState<PrtgLogReason | 'all'>('all')
  const [rows, setRows] = useState<PrtgLogRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    getPrtgLogs({ sensor_id: sensorId, reason: reason === 'all' ? undefined : reason, limit: 200 })
      .then(res => { if (!cancelled) setRows(Array.isArray(res.data?.logs) ? res.data.logs : []) })
      .catch((e: unknown) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : String(e))
        setRows([])
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [sensorId, reason])

  const columns = useMemo<ColumnsType<PrtgLogRow>>(() => [
    {
      title: t('prtg.logs.col.time'),
      dataIndex: 'happened_at',
      width: 170,
      render: (v: string | null | undefined) => fmtTime(v),
    },
    {
      title: t('prtg.logs.col.status'),
      dataIndex: 'status',
      width: 100,
      render: (v: string | null | undefined) => (
        // PRTG 日志的 Warning 不在看板状态集合里，原样显示
        (v || '').toLowerCase() === 'warning'
          ? <Tag color="orange">{t('prtg.logs.warning')}</Tag>
          : <Tag color={statusColor(v)}>{v ? t(statusLabelKey(v)) : NA}</Tag>
      ),
    },
    {
      title: t('prtg.logs.col.reason'),
      dataIndex: 'reason',
      width: 140,
      render: (v: string | null | undefined) => <Tag color={reasonColor(v)}>{t(reasonLabelKey(v))}</Tag>,
    },
    {
      title: t('prtg.logs.col.message'),
      dataIndex: 'message',
      render: (v: string | null | undefined) => v || NA,
    },
  ], [t])

  return (
    <div>
      <Space style={{ marginBottom: 'var(--wh-space-xs)' }} wrap>
        <Text type="secondary">{t('prtg.logs.filter')}</Text>
        <Select
          size="small"
          style={{ minWidth: 180 }}
          value={reason}
          onChange={v => setReason(v as PrtgLogReason | 'all')}
          options={[
            { value: 'all', label: t('prtg.logs.filterAll') },
            ...PRTG_LOG_REASONS.map(r => ({ value: r, label: t(reasonLabelKey(r)) })),
          ]}
        />
        <Text type="secondary" style={{ fontSize: 12 }}>{t('prtg.logs.count', { n: rows.length })}</Text>
      </Space>
      {error && <Alert type="error" showIcon message={t('prtg.logs.loadFailed')} description={error} />}
      <Table<PrtgLogRow>
        rowKey="id"
        size="small"
        columns={columns}
        dataSource={rows}
        loading={loading}
        pagination={rows.length > 20 ? { pageSize: 20, size: 'small' } : false}
        scroll={{ x: 760 }}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={error ? t('prtg.logs.loadFailed') : t('prtg.logs.empty')}
            />
          ),
        }}
      />
    </div>
  )
}
