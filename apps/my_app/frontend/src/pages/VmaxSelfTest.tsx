/* ============================================================================
 * VmaxSelfTest —— XDR 查询自检页（网络 → XDR 查询自检，?page=vmax_selftest）
 * ============================================================================
 * 2026-11-26：用于验证 VMAX 智能诊断的改动是否生效，免去在终端来回贴输出。
 *
 * 输入一个号码，依次检查：
 *   1. RAN 分表清单（serviceType 32）
 *   2. 合并表：1100 / 1300 / 1301 / 32
 *   3. 分表：32 UEMR（fact_lte_uemr）、1300 FLOW、1100 S1MME
 *
 * 逐项串行执行（不并发），避免打满上游同账号的并发上限。
 * 只读查询，不写库（查询日志由后端照常记录）。
 * 访问控制由后端 phone_query_acl 负责，本页不另设门槛。
 * ========================================================================== */

import { useState } from 'react'
import { Alert, Button, Input, Space, Table, Tag, Typography } from 'antd'
import { MobileOutlined, PlayCircleOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'

import { vmaxXdrQuery, vmaxXdrRecords, vmaxXdrTables } from '../api'
import { CardBase } from '../components/cards'

const { Text } = Typography

type Status = 'pending' | 'running' | 'ok' | 'empty' | 'no_imsi' | 'fail'

type Outcome = {
  status: Status
  records?: number
  ms?: number
  detail?: string
}

type Row = { key: string; name: string; outcome: Outcome }

type StepResult = { records: number; empty_reason?: string; note?: string }

type Step = {
  key: string
  name: string
  exec: (msisdn: string, start: string, end: string) => Promise<StepResult>
}

const TIME_FMT = 'YYYY-MM-DD HH:mm'

/** 合并表：一个类型的全部接口流水 */
const mergedStep = (sid: string, label: string): Step => ({
  key: `merged-${sid}`,
  name: `合并表 · ${label} (${sid})`,
  exec: async (msisdn, start, end) => {
    const r = await vmaxXdrQuery({ msisdn, service_type: sid, start, end, page: 1, page_size: 5 })
    const d = r.data || {}
    return { records: Number(d.records || 0), empty_reason: d.empty_reason }
  },
})

/** 分表：某一张具体的表 */
const tableStep = (sid: string, table: string, label: string): Step => ({
  key: `table-${table}`,
  name: `分表 · ${label} (${table})`,
  exec: async (msisdn, start, end) => {
    const r = await vmaxXdrRecords({ msisdn, service_type: sid, table, start, end, page: 1, page_size: 5 })
    const d = r.data || {}
    return { records: Number(d.records || 0), empty_reason: d.empty_reason }
  },
})

const STEPS: Step[] = [
  {
    key: 'tables-32',
    name: 'RAN 分表清单 (32)',
    exec: async () => {
      const r = await vmaxXdrTables('32')
      const n = (r.data?.tables || []).length
      return { records: n, note: `上游返回 ${n} 张表` }
    },
  },
  mergedStep('1100', '控制面信令'),
  mergedStep('1300', '用户面话单'),
  mergedStep('1301', '用户面业务'),
  mergedStep('32', 'RAN 无线侧'),
  tableStep('32', 'fact_lte_uemr', 'UEMR（测量报告）'),
  tableStep('1300', 'fact_5gsa_psup_flow', 'FLOW'),
  tableStep('1100', 'fact_pscp_s1mme', 'S1MME'),
]

function errText(e: any): string {
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (d) return JSON.stringify(d)
  return e?.message || String(e)
}

const STATUS_TAG: Record<Status, { color: string; text: string }> = {
  pending: { color: 'default', text: '等待' },
  running: { color: 'processing', text: '查询中' },
  ok: { color: 'green', text: '通过（有数据）' },
  empty: { color: 'default', text: '无数据' },
  no_imsi: { color: 'orange', text: '无 IMSI 映射' },
  fail: { color: 'red', text: '失败' },
}

export default function VmaxSelfTest() {
  const [msisdn, setMsisdn] = useState('')
  const [running, setRunning] = useState(false)
  const [window_, setWindow] = useState<{ start: string; end: string } | null>(null)
  const [rows, setRows] = useState<Row[]>([])

  const patchRow = (key: string, outcome: Outcome) =>
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, outcome } : r)))

  const run = async () => {
    const m = msisdn.trim()
    if (!m || running) return
    const end = dayjs()
    const start = end.subtract(3, 'hour')
    const s = start.format(TIME_FMT)
    const e = end.format(TIME_FMT)
    setWindow({ start: s, end: e })
    setRunning(true)
    setRows(STEPS.map((st) => ({ key: st.key, name: st.name, outcome: { status: 'pending' } })))

    for (const st of STEPS) {
      patchRow(st.key, { status: 'running' })
      const t0 = performance.now()
      let outcome: Outcome
      try {
        const res = await st.exec(m, s, e)
        const ms = Math.round(performance.now() - t0)
        if (res.records > 0) {
          outcome = { status: 'ok', records: res.records, ms, detail: res.note }
        } else if (res.empty_reason === 'no_imsi') {
          outcome = {
            status: 'no_imsi', records: 0, ms,
            detail: 'RAN 表按 IMSI 记录，该号码在 1300/1100 中查不到 IMSI，无法查无线侧',
          }
        } else {
          outcome = {
            status: 'empty', records: 0, ms,
            detail: res.note || '时间窗内无记录（可能没有这类业务，或超出上游保留期）',
          }
        }
      } catch (err) {
        outcome = { status: 'fail', ms: Math.round(performance.now() - t0), detail: errText(err) }
      }
      patchRow(st.key, outcome)
    }
    setRunning(false)
  }

  const count = (s: Status) => rows.filter((r) => r.outcome.status === s).length
  const done = rows.length > 0 && rows.every((r) => r.outcome.status !== 'pending' && r.outcome.status !== 'running')
  const nFail = count('fail')
  const nNoImsi = count('no_imsi')
  const nEmpty = count('empty')
  const nOk = count('ok')

  const summaryType: 'success' | 'warning' | 'error' =
    nFail > 0 ? 'error' : (nNoImsi > 0 || nEmpty > 0) ? 'warning' : 'success'

  const columns = [
    { title: '检查项', dataIndex: 'name', width: 300 },
    {
      title: '结果', width: 150,
      render: (_: unknown, r: Row) => {
        const tag = STATUS_TAG[r.outcome.status]
        return <Tag color={tag.color}>{tag.text}</Tag>
      },
    },
    {
      title: '条数', width: 90,
      render: (_: unknown, r: Row) => (r.outcome.records == null ? '—' : r.outcome.records),
    },
    {
      title: '耗时', width: 90,
      render: (_: unknown, r: Row) => (r.outcome.ms == null ? '—' : `${r.outcome.ms} ms`),
    },
    {
      title: '说明 / 原始错误',
      render: (_: unknown, r: Row) => (
        <Text style={{ fontSize: 12, wordBreak: 'break-all' }}>{r.outcome.detail || '—'}</Text>
      ),
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)', maxWidth: 1100 }}>
      <CardBase title="XDR 查询自检">
        <Space wrap>
          <Input
            allowClear
            prefix={<MobileOutlined style={{ color: 'var(--wh-text-dim)' }} />}
            placeholder="输入完整号码，如 231xxxxxxxxx"
            value={msisdn}
            onChange={(e) => setMsisdn(e.target.value)}
            onPressEnter={() => void run()}
            style={{ width: 260 }}
            maxLength={24}
          />
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={running}
            disabled={!msisdn.trim()}
            onClick={() => void run()}
          >
            运行自检（最近 3 小时）
          </Button>
        </Space>
        <div style={{ marginTop: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            逐项串行执行，共 {STEPS.length} 项。无数据不等于正常，需结合时段与业务判断。
          </Text>
        </div>
      </CardBase>

      {window_ && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          查询时间窗：{window_.start} ~ {window_.end}
        </Text>
      )}

      {running && (
        <Alert type="info" showIcon message="正在依次检查，请稍候…" />
      )}

      {done && (
        <Alert
          type={summaryType}
          showIcon
          message={`通过 ${nOk} 项 · 无数据 ${nEmpty} 项 · 无 IMSI ${nNoImsi} 项 · 失败 ${nFail} 项`}
          description={
            nFail > 0
              ? '有失败项，看下方「说明 / 原始错误」列。失败通常是上游鉴权、并发或参数问题，请把原始错误发给开发。'
              : nNoImsi > 0
                ? '无 IMSI 映射：该号码在 1300/1100 找不到 IMSI，RAN 无线侧无法查询。'
                : nEmpty > 0
                  ? '部分项无数据：可能是该号码没有这类业务，也可能时段超出保留期，换一个有业务的号码再试。'
                  : '所有项都有数据，改动在该号码上生效。'
          }
        />
      )}

      <CardBase>
        <Table
          size="small"
          bordered
          rowKey="key"
          columns={columns}
          dataSource={rows}
          pagination={false}
          locale={{ emptyText: '输入号码后点击「运行自检」' }}
        />
      </CardBase>
    </div>
  )
}
