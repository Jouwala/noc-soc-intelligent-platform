/* ==================== TT 州维度统计：下钻明细弹窗 ====================
 * 打开方式与 page=tt 一致：DraggableClickModal（可拖拽、Esc 关闭、点遮罩不关）。
 * 行数与点击那个格子的数字严格同源 —— 后端 overview 与 drilldown 共用同一份取数，
 * 前端不做二次过滤（除了这里的模糊搜索，它只影响「当前展示」，导出用完整结果）。
 * ================================================================ */
import { useMemo, useState } from 'react'
import { Alert, Button, Empty, Input, Space, Table, Tag, Typography } from 'antd'
import { DownloadOutlined, SearchOutlined } from '@ant-design/icons'

import { useI18n } from '../../i18n'
import { WH } from '../../theme'
import DraggableClickModal from '../../components/DraggableClickModal'
import { stateLabel, type DrillResult, type DrillRow } from './ttStateTypes'

const { Text } = Typography

interface Props {
  open: boolean
  anchor: { x: number; y: number } | null
  loading: boolean
  data: DrillResult | null
  title: string
  onClose: () => void
  onExport: (rows: DrillRow[]) => void
}

export default function TtStateDrillModal({ open, anchor, loading, data, title, onClose, onExport }: Props) {
  const { t } = useI18n()
  const [keyword, setKeyword] = useState('')

  const rows = data?.rows || []
  const shown = useMemo(() => {
    const q = keyword.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(r =>
      [r.tt_no, r.wo_no, r.site_name, r.state, r.tt_status, r.wo_status]
        .some(v => String(v || '').toLowerCase().includes(q)))
  }, [rows, keyword])

  const trunc = data?.meta?.truncated
  const columns = useMemo(() => [
    { title: t('ttState.drill.col.tt'), dataIndex: 'tt_no', width: 150, render: (v: string) => <Text style={{ fontFamily: 'monospace' }}>{v || '—'}</Text> },
    { title: t('ttState.drill.col.wo'), dataIndex: 'wo_no', width: 150, render: (v: string) => <Text style={{ fontFamily: 'monospace' }}>{v || '—'}</Text> },
    {
      title: t('ttState.drill.col.state'), dataIndex: 'state', width: 130,
      render: (v: string) => stateLabel(v, t),
    },
    { title: t('ttState.drill.col.site'), dataIndex: 'site_name', width: 180, ellipsis: true, render: (v: string) => v || '—' },
    { title: t('ttState.drill.col.occur'), dataIndex: 'alarm_occur_time', width: 160, render: (v: string) => v || '—' },
    {
      title: t('ttState.drill.col.clear'), dataIndex: 'alarm_clear_time', width: 160,
      render: (v: string) => v || <Text type="secondary">{t('ttState.drill.noClear')}</Text>,
    },
    { title: t('ttState.drill.col.ttStatus'), dataIndex: 'tt_status', width: 170, render: (v: string) => v || '—' },
    { title: t('ttState.drill.col.woStatus'), dataIndex: 'wo_status', width: 150, render: (v: string) => v || '—' },
    {
      title: t('ttState.drill.col.flags'), key: 'flags', width: 210,
      render: (_: unknown, r: DrillRow) => (
        <Space size={4} wrap>
          {r.alarm_uncleared && <Tag color="red">{t('ttState.tag.alarm')}</Tag>}
          {!r.wo_closed && <Tag color="orange">{t('ttState.tag.wo')}</Tag>}
          {r.tt_unclosed && <Tag color="purple">{t('ttState.tag.tt')}</Tag>}
          {!r.alarm_uncleared && r.wo_closed && !r.tt_unclosed
            && <Tag color="green">{t('ttState.tag.closed')}</Tag>}
        </Space>
      ),
    },
  ], [t])

  return (
    <DraggableClickModal
      open={open}
      title={title}
      anchor={anchor}
      onClose={onClose}
      headerExtra={
        <Space size={8}>
          <Tag color="blue">{t('ttState.drill.count', { n: data?.meta?.count ?? 0 })}</Tag>
          <Button
            size="small"
            icon={<DownloadOutlined />}
            disabled={!rows.length}
            onClick={() => onExport(rows)}
          >
            {t('ttState.export')}
          </Button>
        </Space>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-sm)' }}>
        <Space wrap>
          <Input
            allowClear
            prefix={<SearchOutlined style={{ color: WH.textDim }} />}
            placeholder={t('ttState.drill.search')}
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            style={{ width: 280 }}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('ttState.drill.showing', { shown: shown.length, total: rows.length })}
          </Text>
        </Space>

        {trunc && (
          <Alert
            type="warning"
            showIcon
            message={t('ttState.drill.truncated', {
              limit: data?.meta?.limit ?? 0, shown: rows.length,
            })}
          />
        )}

        {rows.length ? (
          <Table<DrillRow>
            rowKey={r => `${r.tt_no}-${r.wo_no}`}
            size="small"
            loading={loading}
            columns={columns}
            dataSource={shown}
            pagination={{ pageSize: 15, showSizeChanger: true, pageSizeOptions: [15, 30, 50] }}
            scroll={{ x: 1200 }}
          />
        ) : (
          <div style={{ padding: '32px 0' }}>
            <Empty description={loading ? t('ttState.loading') : t('ttState.drill.empty')} />
          </div>
        )}
      </div>
    </DraggableClickModal>
  )
}
