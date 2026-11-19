// 任务执行历史弹窗（P3#10 拆分自 DataSync.tsx）
import { useState, useEffect } from 'react'
import { Button, Collapse, List, Modal, Space, Tag, Typography, message } from 'antd'
import {
  CheckCircleFilled,
  ClockCircleFilled,
  CloseCircleFilled,
  ExclamationCircleFilled,
  HistoryOutlined,
  DownloadOutlined,
} from '@ant-design/icons'
import api from '../../api'
import { WH } from '../../theme'
import { formatDateTime } from '../../datetime'
import { ExplainIcon, ExplainPopover } from '../../components/MetricExplain'
import type { TaskDetail, RunRecord } from './types'
import { useI18n } from '../../i18n'

const { Text } = Typography

// 解析 result_json（任务函数返回的原始 dict 字符串）。解析失败按「没有附加信息」处理，
// 不影响原有的成功/失败判断。
// 用途：Run History 要区分「真失败」与「cmdb 站点缺档」——后者后端记
// status=pending_cmdb、success=true，属配置待补而不是任务出错（2026-11-17 如意要求），
// 不能显示成红色 ❌ Error。
function parseRunResult(resultJson?: string | null): Record<string, unknown> | null {
  if (!resultJson) return null
  try {
    const v: unknown = JSON.parse(resultJson)
    return v && typeof v === 'object' ? (v as Record<string, unknown>) : null
  } catch {
    return null
  }
}

export function TaskHistoryModal({
  open, task, locale, onClose,
}: {
  open: boolean
  task: TaskDetail | null
  locale: 'zh' | 'en'
  onClose: () => void
}) {
  const { t } = useI18n()
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [stats, setStats] = useState<{
    success_rate: number | null; avg_elapsed: number | null; max_elapsed: number | null;
    total_runs: number; success_runs?: number; failed_runs?: number
  } | null>(null)
  const [visible, setVisible] = useState(20)

  useEffect(() => {
    if (open && task) {
      api.get(`/tasks/${task.task_id}/history?limit=200`)
        .then(res => setRuns(res.data.history || []))
        .catch(() => setRuns([]))
      api.get(`/tasks/${task.task_id}/stats`)
        .then(res => setStats(res.data))
        .catch(() => setStats(null))
    } else {
      setRuns([])
      setStats(null)
      setVisible(20)
    }
  }, [open, task?.task_id])

  const exportCsv = () => {
    if (!runs.length) return
    const header = ['task_id', 'task_name', 'success', 'trigger', 'started_at', 'completed_at', 'elapsed_seconds', 'error']
    const lines = [header.join(',')]
    runs.forEach(r => {
      lines.push([
        r.task_id, r.task_name, r.success ? '1' : '0', r.trigger,
        r.started_at, r.completed_at, r.elapsed_seconds,
        `"${(r.error || '').replace(/"/g, '""').replace(/\n/g, ' ')}"`,
      ].join(','))
    })
    const blob = new Blob([`\uFEFF${lines.join('\n')}`], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `task_history_${task?.task_id || 'all'}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  // 展示 result_json.warnings（如 cm_data_sync 自动剔除的失效网元，含具体原因）
  const renderWarnings = (resultJson: string, loc: 'zh' | 'en') => {
    try {
      const rj = JSON.parse(resultJson)
      const warnings: { flow?: string; ne?: string; reason?: string }[] = rj?.warnings
      if (!Array.isArray(warnings) || warnings.length === 0) return null
      return (
        <div style={{ marginTop: 'var(--wh-space-sm)' }}>
          <Text style={{ fontSize: '0.75rem', color: '#ffa940', fontWeight: 600 }}>
            ⚠️ {loc === 'zh' ? '跳过的失效网元' : 'Skipped NEs'}
          </Text>
          {warnings.map((w, i) => (
            <div key={i} style={{ marginTop: 'var(--wh-space-xs)', wordBreak: 'break-word' }}>
              <Tag color="orange" style={{ fontSize: '0.625rem', marginRight: 'var(--wh-space-xs)' }}>{w.flow || '-'}</Tag>
              <code style={{ color: '#ffd591', fontSize: '0.6875rem' }}>{w.ne || '-'}</code>
              <div style={{ fontSize: '0.6875rem', color: WH.textDim, marginTop: '0.125rem' }}>{w.reason || ''}</div>
            </div>
          ))}
        </div>
      )
    } catch {
      return null
    }
  }

  return (
    <Modal
      title={
        <span style={{ color: WH.text }}>
          <HistoryOutlined style={{ marginRight: 8, color: WH.teal }} />
          {locale === 'zh' ? `执行历史 - ${task?.name}` : `Run History - ${task?.name}`}
        </span>
      }
      open={open}
      onCancel={onClose}
      footer={null}
      width={'min(95vw, 43.75rem)' as any}
      styles={{
        root: { background: WH.bg },
        header: { background: 'var(--wh-page-bg)', borderBottom: '1px solid rgba(0, 217, 255,0.15)' },
        body: { background: WH.bg },
      }}
    >
      {stats && stats.total_runs > 0 && (
        <div style={{ display: 'flex', gap: 'var(--wh-space-md)', marginBottom: 'var(--wh-space-sm)', flexWrap: 'wrap' }}>
          <ExplainPopover text={t('sync.explain.thTotal', { n: stats.total_runs, ok: stats.success_runs ?? 0, bad: stats.failed_runs ?? 0 })}>
            <Text style={{ fontSize: '0.75rem', color: WH.textMuted }}>
              {locale === 'zh' ? '总运行' : 'Total'}: <b style={{ color: WH.text }}>{stats.total_runs}</b>
            </Text>
          </ExplainPopover>
          <ExplainPopover text={t('sync.explain.thSuccess', { rate: stats.success_rate == null ? '-' : `${(stats.success_rate * 100).toFixed(1)}%` })}>
            <Text style={{ fontSize: '0.75rem', color: WH.textMuted }}>
              {locale === 'zh' ? '成功率' : 'Success'}:{' '}
              <b style={{ color: stats.success_rate != null && stats.success_rate >= 0.8 ? '#52c41a' : '#ff6b6b' }}>
                {stats.success_rate != null ? `${(stats.success_rate * 100).toFixed(1)}%` : '-'}
              </b>
            </Text>
          </ExplainPopover>
          <ExplainPopover text={t('sync.explain.thAvg', { v: stats.avg_elapsed == null ? '-' : `${stats.avg_elapsed}s` })}>
            <Text style={{ fontSize: '0.75rem', color: WH.textMuted }}>
              {locale === 'zh' ? '平均耗时' : 'Avg'}: <b style={{ color: WH.text }}>{stats.avg_elapsed != null ? `${stats.avg_elapsed}s` : '-'}</b>
            </Text>
          </ExplainPopover>
          <ExplainPopover text={t('sync.explain.thMax', { v: stats.max_elapsed == null ? '-' : `${stats.max_elapsed}s` })}>
            <Text style={{ fontSize: '0.75rem', color: WH.textMuted }}>
              {locale === 'zh' ? '最大耗时' : 'Max'}: <b style={{ color: WH.text }}>{stats.max_elapsed != null ? `${stats.max_elapsed}s` : '-'}</b>
            </Text>
          </ExplainPopover>
        </div>
      )}
      {runs.length > 0 ? (
        <>
        <List
          size="small"
          dataSource={runs.slice(0, visible)}
          renderItem={(run: RunRecord) => {
            const rj = parseRunResult(run.result_json)
            // cmdb 站点缺档：任务本身没出错（success=true），只是 cmdb 里没有对应站点。
            // 后端 status=pending_cmdb 专门标这类（fiber_assign_service.apply_outcome），
            // 这里按橙色「提示」渲染（2026-11-17 如意要求：提示照旧，但不报错）。
            const pendingCmdb = rj?.status === 'pending_cmdb'
            const cmdbNotice = pendingCmdb ? String(rj?.message || '') : ''
            // 延后不是失败：邮箱通道降级时本轮没执行，用琥珀色与「失败」区分开
            const deferred = !run.success && run.trigger === 'deferred'
            return (
            <List.Item style={{
              background: pendingCmdb || deferred
                ? 'rgba(250, 173, 20, 0.05)'
                : (run.success ? 'rgba(82, 196, 26, 0.05)' : 'rgba(255, 77, 79, 0.05)'),
              borderRadius: 'var(--wh-radius-md)',
              padding: 'var(--wh-space-sm) var(--wh-space-md)',
              marginBottom: 'var(--wh-space-xs)',
              border: `1px solid ${pendingCmdb || deferred
                ? 'rgba(250, 173, 20, 0.25)'
                : (run.success ? 'rgba(82, 196, 26, 0.2)' : 'rgba(255, 77, 79, 0.2)')}`,
            }}>
              <List.Item.Meta
                avatar={pendingCmdb
                  ? <ExclamationCircleFilled style={{ color: '#faad14', fontSize: '1.125rem' }} />
                  : deferred
                    ? <ClockCircleFilled style={{ color: '#faad14', fontSize: '1.125rem' }} />
                    : (run.success
                      ? <CheckCircleFilled style={{ color: '#52c41a', fontSize: '1.125rem' }} />
                      : <CloseCircleFilled style={{ color: '#ff4d4f', fontSize: '1.125rem' }} />)
                }
                title={
                  <Space>
                    <Tag color={pendingCmdb ? 'orange' : deferred ? 'gold' : (run.success ? 'green' : 'red')}>
                      {pendingCmdb
                        ? t('sync.runStatus.pendingCmdb')
                        : deferred
                          ? (locale === 'zh' ? '已延后' : 'Deferred')
                          : (run.success ? (locale === 'zh' ? '成功' : 'Success') : (locale === 'zh' ? '失败' : 'Failed'))}
                    </Tag>
                    <Text style={{ fontSize: '0.75rem', color: WH.textMuted }}>
                      {run.trigger === 'manual' ? '🔧' : deferred ? '⏸' : '⏰'} {formatDateTime(run.started_at, { zone: true })}
                    </Text>
                  </Space>
                }
                description={
                  <div>
                    <Text style={{ fontSize: '0.75rem', color: WH.textDim }}>⏱ {(run.elapsed_seconds ?? 0).toFixed(2)}s</Text>
                    {cmdbNotice && (
                      <div style={{ marginTop: 'var(--wh-space-sm)' }}>
                        <Text style={{ fontSize: '0.75rem', color: '#faad14', fontWeight: 600 }}>
                          ⚠ {t('sync.runNotice.pendingCmdb')}
                        </Text>
                        <pre style={{
                          margin: 'var(--wh-space-xs) 0 0', padding: 'var(--wh-space-sm)', fontSize: '0.6875rem', lineHeight: 1.5,
                          color: '#ffe58f', background: 'rgba(250,173,20,0.08)', borderRadius: 'var(--wh-radius-md)',
                          border: '1px solid rgba(250,173,20,0.25)', whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word', maxHeight: '10rem', overflow: 'auto',
                        }}>
                          {cmdbNotice}
                        </pre>
                      </div>
                    )}
                    {run.error && (
                      <div style={{ marginTop: 'var(--wh-space-sm)' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <Text style={{ fontSize: '0.75rem', color: '#ff6b6b', fontWeight: 600 }}>
                            ❌ {locale === 'zh' ? '错误信息' : 'Error'}
                          </Text>
                          <Button
                            type="link"
                            size="small"
                            style={{ fontSize: '0.6875rem', padding: 0, height: 'auto', color: WH.teal }}
                            onClick={() => {
                              navigator.clipboard?.writeText(run.error || '')
                                .then(() => message.success(locale === 'zh' ? '已复制错误' : 'Copied'))
                                .catch(() => {})
                            }}
                          >
                            {locale === 'zh' ? '复制' : 'Copy'}
                          </Button>
                        </div>
                        <pre style={{
                          margin: 'var(--wh-space-xs) 0 0', padding: 'var(--wh-space-sm)', fontSize: '0.6875rem', lineHeight: 1.5,
                          color: '#ffb3b3', background: 'rgba(255,77,79,0.08)', borderRadius: 'var(--wh-radius-md)',
                          border: '1px solid rgba(255,77,79,0.2)', whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word', maxHeight: '10rem', overflow: 'auto',
                        }}>
                          {run.error}
                        </pre>
                        {run.error_trace && (
                          <Collapse
                            ghost
                            size="small"
                            style={{ marginTop: 'var(--wh-space-sm)' }}
                            items={[{
                              key: 'trace',
                              label: <Text style={{ fontSize: '0.6875rem', color: WH.textMuted }}>{locale === 'zh' ? '堆栈 / 详情' : 'Stack Trace'}</Text>,
                              children: (
                                <pre style={{
                                  margin: 0, padding: 'var(--wh-space-sm)', fontSize: '0.65625rem', lineHeight: 1.45,
                                  color: WH.textDim, background: 'rgba(0,0,0,0.25)', borderRadius: 'var(--wh-radius-md)',
                                  whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '15rem', overflow: 'auto',
                                }}>
                                  {run.error_trace}
                                </pre>
                              ),
                            }]}
                          />
                        )}
                      </div>
                    )}
                    {run.result_json && renderWarnings(run.result_json, locale)}
                  </div>
                }
              />
            </List.Item>
            )
          }}
        />
        {runs.length > visible && (
          <div style={{ textAlign: 'center', marginTop: 'var(--wh-space-sm)' }}>
            <Button size="small" onClick={() => setVisible(v => v + 20)} style={{ color: WH.text }}>
              {locale === 'zh' ? `加载更多（已显示 ${visible}/${runs.length}）` : `Load More (${visible}/${runs.length})`}
            </Button>
            <div style={{ marginTop: 4 }}>
              <ExplainIcon text={t('sync.explain.thShown', { shown: visible, fetched: runs.length })} />
            </div>
          </div>
        )}
        <div style={{ textAlign: 'right', marginTop: 'var(--wh-space-sm)' }}>
          <Button size="small" icon={<DownloadOutlined />} onClick={exportCsv} disabled={!runs.length}
            style={{ color: WH.text, borderColor: 'rgba(0, 217, 255,0.25)' }}>
            {locale === 'zh' ? '导出 CSV' : 'Export CSV'}
          </Button>
        </div>
        </>
      ) : (
        <div style={{ textAlign: 'center', color: WH.textMuted, padding: 'var(--wh-space-xl)' }}>
          {locale === 'zh' ? '暂无执行记录' : 'No run history'}
        </div>
      )}
    </Modal>
  )
}
