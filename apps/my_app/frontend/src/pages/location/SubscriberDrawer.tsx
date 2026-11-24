/* ============================================================================
 * location/SubscriberDrawer —— 位置页点「用户轨迹小区」后的右侧订阅者弹窗
 * ============================================================================
 *
 * 需求（2026-11-23）：弹窗内容与 `?page=vmax_user_query` 对齐 ——
 *   Subscriber Info / Terminal Info / User Track Cells / Evidence timeline /
 *   Fault analysis / Root cause & recommendations。
 *
 * 数据全部来自页面已经跑过的一次 VMAX 诊断（pages/useVmaxDiagnosis 的返回值），
 * 本抽屉**不重复取数**：地图上的轨迹小区、抽屉里的六段内容看的是同一次查询。
 * 组件与 vmax_user_query / vmax_user_cca 复用同一批：
 *   · VmaxCcaCards          —— 用户 / 终端 / 驻留小区 / 根因（CCA）
 *   · VmaxWorkspaceAnalysis —— 故障分析（链路 + 五段指标 + 专卡 + 流量 + 证据时间线）
 *   · VmaxWorkspaceVerdict  —— 根因与建议（排序 + 待补证据常驻提示）
 *   · VmaxEvidenceTimeline 由 Analysis 内嵌渲染（同一份 timeline，不再重复挂一次）
 *
 * 抽屉宽度比站点抽屉宽：这些卡里有多列表格（小区 / 根因），窄了会横向滚动。
 * ========================================================================== */
import { useMemo, useState } from 'react'
import {
  Alert, Button, Descriptions, Drawer, Empty, Spin, Table, Tag, Typography,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

import { useI18n } from '../../i18n'
import DraggableClickModal from '../../components/DraggableClickModal'
import VmaxCcaCards, { type CauseItem, type CellRecord } from '../../components/VmaxCcaCards'
import VmaxWorkspaceAnalysis from '../../components/vmax/VmaxWorkspaceAnalysis'
import VmaxWorkspaceVerdict from '../../components/vmax/VmaxWorkspaceVerdict'
import VmaxEvidenceDrawer from '../../components/vmax/VmaxEvidenceDrawer'
import { VmaxWindowContext } from '../vmaxWindowContext'
import { PAGE_RETENTION_HOURS } from '../vmaxWindow'
import { buildWorkspaceCorrelation, type EvidenceKind } from '../../lib/vmaxWorkspaceRules'
import type { VmaxDiag } from '../useVmaxDiagnosis'

const { Text } = Typography

type KpiItem = { code: string; name: string; value: unknown }
const fmt = (v: unknown) => (v == null || v === '' || v === '--' ? '—' : String(v))

export default function SubscriberDrawer({
  open, msisdn, diag, onClose,
}: {
  open: boolean
  msisdn: string
  /** useVmaxDiagnosis 的返回值（页面持有，抽屉只读） */
  diag: VmaxDiag
  onClose: () => void
}) {
  const { t } = useI18n()

  /* 证据抽屉：VmaxWorkspaceAnalysis / Verdict 里的「查看证据」按钮落在这里 */
  const [evidence, setEvidence] = useState<{ kind: EvidenceKind | null; open: boolean }>({
    kind: null, open: false,
  })
  const openEvidence = (kind: EvidenceKind) => setEvidence({ kind, open: true })

  /* KPI / 根因明细弹窗（与 vmax_user_query 同款 DraggableClickModal） */
  const [kpiCell, setKpiCell] = useState<CellRecord | null>(null)
  const [kpiItems, setKpiItems] = useState<KpiItem[]>([])
  const [kpiAnchor, setKpiAnchor] = useState<{ x: number; y: number } | null>(null)
  const [kpiOpen, setKpiOpen] = useState(false)
  const [causeItem, setCauseItem] = useState<CauseItem | null>(null)
  const [causeOpen, setCauseOpen] = useState(false)

  const ws = useMemo(() => buildWorkspaceCorrelation({
    payload: diag.diag,
    sources: diag.sources,
    scores: diag.scores,
    voice: diag.voice,
    ott: diag.ott,
    game: diag.game,
    radio: diag.radio,
    timeline: diag.timeline,
    cca: diag.result,
    complaint: diag.complaint,
  }), [diag.diag, diag.sources, diag.scores, diag.voice, diag.ott, diag.game, diag.radio, diag.timeline, diag.result, diag.complaint])

  const ctxValue = {
    range: diag.range, setRange: diag.setRange,
    retentionHours: PAGE_RETENTION_HOURS, query: diag.query,
  }

  const kpiColumns = [
    { title: t('vmaxq.kpiName'), dataIndex: 'name', ellipsis: true },
    { title: t('vmaxq.kpiValue'), dataIndex: 'value', width: 110, align: 'right' as const, render: fmt },
    {
      title: t('vmaxq.kpiCode'), dataIndex: 'code', width: 150, ellipsis: true,
      render: (v: string) => <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text>,
    },
  ]

  const loading = diag.loading || diag.diagLoading
  const hasAnything = diag.searched || !!diag.result || !!diag.diag

  return (
    <VmaxWindowContext.Provider value={ctxValue}>
      <Drawer
        open={open}
        onClose={onClose}
        placement="right"
        width="min(96vw, 82rem)"
        title={`${t('loc.sub.title')} · ${msisdn || '—'}`}
        destroyOnHidden
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>
          {!!diag.diagError && (
            <Alert
              type="error" showIcon message={diag.diagError}
              action={<Button size="small" icon={<ReloadOutlined />} onClick={diag.refresh}>{t('vmaxq.retry')}</Button>}
            />
          )}
          {!!diag.error && (
            <Alert
              type="error" showIcon message={diag.error}
              action={<Button size="small" icon={<ReloadOutlined />} onClick={diag.refresh}>{t('vmaxq.retry')}</Button>}
            />
          )}

          {loading && !hasAnything && (
            <div style={{ textAlign: 'center', padding: 48 }}>
              <Spin size="large" />
              <div style={{ marginTop: 10 }}><Text type="secondary">{t('loc.sub.loading')}</Text></div>
            </div>
          )}

          {!loading && !hasAnything && (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">{t('loc.sub.empty')}</Text>} />
          )}

          {!!diag.result?.failures?.length && (
            <Alert type="warning" showIcon message={t('vmaxq.partialFail')} description={diag.result.failures.join('；')} />
          )}

          {/* 1) Subscriber Info / Terminal Info / User Track Cells（+ CCA 根因） */}
          {diag.result?.found && (
            <VmaxCcaCards
              result={diag.result}
              onOpenKpi={(cell, e) => {
                setKpiCell(cell)
                setKpiItems(diag.result?.cells?.cell_kpi?.[String(cell.cell)] || [])
                setKpiAnchor({ x: e.clientX, y: e.clientY })
                setKpiOpen(true)
              }}
              onOpenCause={(item) => { setCauseItem(item); setCauseOpen(true) }}
            />
          )}
          {diag.searched && diag.result && !diag.result.found && (
            <Alert
              type="warning" showIcon
              message={t('vmaxq.notFound', { msisdn: diag.result.msisdn || msisdn })}
              description={diag.result.msg ? `${t('vmaxq.upstreamMsg')}：${diag.result.msg}` : undefined}
            />
          )}

          {/* 2) Fault analysis（含 Evidence timeline：同一份 timeline，不重复渲染） */}
          <VmaxWorkspaceAnalysis
            metrics={ws.metrics}
            chain={ws.chain}
            complaint={diag.complaint}
            voice={diag.voice}
            ott={diag.ott}
            game={diag.game}
            timeline={ws.enrichedTimeline}
            radio={diag.radio}
            flowXdr={diag.flowXdr}
            scores={diag.scores}
            cca={diag.result}
            loading={diag.diagLoading}
            onOpenEvidence={openEvidence}
          />

          {/* 3) Root cause & recommendations */}
          <VmaxWorkspaceVerdict
            rankedCauses={ws.rankedCauses}
            recommendations={ws.recommendations}
            ccaError={diag.error}
            ccaLoading={diag.loading}
            diagLoading={diag.diagLoading}
            diagLoaded={diag.loadedKeys.length}
            diagTotal={diag.diagTotal}
            hypotheses={diag.verdict?.hypotheses}
            missingEvidence={diag.verdict?.missingEvidence}
            onOpenEvidence={openEvidence}
          />

          {!loading && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('loc.sub.footnote')}
            </Text>
          )}
        </div>
      </Drawer>

      {/* 证据抽屉（嵌套） */}
      <VmaxEvidenceDrawer
        kind={evidence.kind}
        open={evidence.open}
        onClose={() => setEvidence((s) => ({ ...s, open: false }))}
        msisdn={msisdn}
        ctx={ctxValue}
        radio={diag.radio}
        diagLoading={diag.diagLoading}
        flowXdr={diag.flowXdr}
        cca={diag.result}
      />

      {/* KPI 明细 */}
      <DraggableClickModal
        open={kpiOpen}
        title={`${t('vmaxq.kpiTitle')} · ${kpiCell?.cellname || kpiCell?.cellkey || ''}`}
        anchor={kpiAnchor}
        onClose={() => setKpiOpen(false)}
      >
        {kpiItems.length ? (
          <Table
            rowKey={(r) => r.code}
            columns={kpiColumns}
            dataSource={kpiItems}
            pagination={{ pageSize: 12 }}
            size="small"
            scroll={{ x: 460 }}
          />
        ) : (
          <Text type="secondary">{t('vmaxq.noKpi')}</Text>
        )}
      </DraggableClickModal>

      {/* 根因明细 */}
      <DraggableClickModal
        open={causeOpen}
        title={causeItem?.rule_name || t('vmaxq.causesCard')}
        onClose={() => setCauseOpen(false)}
      >
        {causeItem && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 380 }}>
            <Descriptions size="small" bordered column={1} items={[
              {
                key: 'conf', label: t('vmaxq.cause.confidence'),
                children: <Tag color={causeItem.confidence >= 90 ? 'red' : 'orange'}>{causeItem.confidence}%</Tag>,
              },
              {
                key: 'obj', label: t('vmaxq.cause.object'),
                children: `${causeItem.object_name}（${causeItem.object_type}）`,
              },
              {
                key: 'area', label: t('vmaxq.cause.area'),
                children: [causeItem.province, causeItem.city, causeItem.vendor].filter(Boolean).join(' / ') || '—',
              },
              { key: 'poor', label: t('vmaxq.cause.poortime'), children: fmt(causeItem.poortime) },
            ]} />
            <div>
              <Text strong>{t('vmaxq.cause.description')}</Text>
              <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{causeItem.description}</div>
            </div>
            {causeItem.solution && (
              <div>
                <Text strong>{t('vmaxq.cause.solution')}</Text>
                <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{causeItem.solution}</div>
              </div>
            )}
            {!!causeItem.app_info?.length && (
              <div>
                <Text strong>{t('vmaxq.cause.timeline')}</Text>
                <Table
                  style={{ marginTop: 4 }}
                  rowKey={(r) => r.clttime + r.judge}
                  columns={[
                    { title: t('vmaxq.cause.time'), dataIndex: 'clttime', width: 140 },
                    { title: t('vmaxq.cause.judge'), dataIndex: 'judge' },
                  ]}
                  dataSource={causeItem.app_info}
                  pagination={false}
                  size="small"
                />
              </div>
            )}
          </div>
        )}
      </DraggableClickModal>
    </VmaxWindowContext.Provider>
  )
}
