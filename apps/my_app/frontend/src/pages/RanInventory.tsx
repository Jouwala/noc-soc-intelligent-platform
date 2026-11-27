/* ============================================================================
 * RAN 资产台账（?page=ran_inventory）
 * ----------------------------------------------------------------------------
 * 数据源：RAN UME 的三类资产接口 + 网元表，
 *   经 `backend/services/ran_inventory/` 聚合，由 `backend/routers/ran_inventory.py` 下发。
 *
 * 口径（页面只展示、不重算）：
 *   · **只统计在线网元** —— `connectionState = 1`。离线网元只进抬头的在线率分母，
 *     其名下资产不进任何卡片、图表与台账行。
 *   · 硬件按 `inventoryunittype` 分组；机龄取该网元最早一台设备的制造日期。
 *   · 型号：板卡/射频取 `packfunction`，天馈取 `vendorunittypename`（见后端注释）。
 *
 * 两个视图（同一个表）：
 *   · 站点汇总 —— 数量 + 三列型号构成（「型号 数量 · …」，悬停看全部）
 *   · 型号透视 —— 每个型号一列，列头按 板卡/射频/天馈 分组，组头带该组总量
 *
 * 项目统一约定（见 AGENTS.md）：
 *   ✅ 卡片 CardBase / StatCard，不 import antd Card
 *   ✅ 导出 utils/excelReport（UNIFIED_EXCEL_V1），不裸写 xlsx
 *   ✅ 下钻 DraggableClickModal；明细在 pages/ranInventory/RanInventoryDetail.tsx
 *   ✅ 口径说明 CalcChainNote（默认收起）；数字悬浮 MetricExplain
 *   ✅ 文案 t()（中英两处），颜色 WH / ECharts 用 DARK·LIGHT 实值
 * ========================================================================== */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Col, Empty, Input, Row, Segmented, Select, Space, Switch, Table, Typography,
} from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'

import ReactECharts from '../components/ECharts'
import { useI18n } from '../i18n'
import { WH, useTheme } from '../theme'
import { CardBase, StatCard } from '../components/cards'
import DraggableClickModal from '../components/DraggableClickModal'
import CalcChainNote from '../components/CalcChainNote'
import { ExplainIcon } from '../components/MetricExplain'
import { getRanInventoryLedger, getRanInventoryNe } from '../api'
import RanInventoryDetail from './ranInventory/RanInventoryDetail'
import buildCharts from './ranInventory/ranInventoryCharts'
import { buildSummaryColumns, buildPivotColumns } from './ranInventory/ranInventoryColumns'
import { exportDetail, exportLedger, exportPivot } from './ranInventory/ranInventoryExport'
import { buildCatColumns, buildModelIndex, MODEL_CAT_LABEL, MODEL_CATS, type ModelCat } from './ranInventory/ranInventoryModels'
import { type Detail, type Ledger, type NeRow } from './ranInventory/ranInventoryTypes'

const { Text } = Typography

type View = 'summary' | 'models'
const TOPN_OPTIONS = [6, 8, 10, 12]

export default function RanInventory() {
  const { t } = useI18n()
  const { isDark, colors } = useTheme()

  const [data, setData] = useState<Ledger | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [q, setQ] = useState('')
  const [moc, setMoc] = useState('')
  const [oldOnly, setOldOnly] = useState(false)
  const [view, setView] = useState<View>('summary')
  const [topN, setTopN] = useState(10)
  // 型号透视里只看某一类时，36 列会压成横向滚动条 —— 给一个类别收窄
  const [cat, setCat] = useState<'ALL' | ModelCat>('ALL')

  const [drill, setDrill] = useState<{ ne: NeRow; anchor?: { x: number; y: number } } | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  /* ---- 取数 ---- */
  const load = useCallback(async (refresh = false) => {
    setLoading(true)
    setError('')
    try {
      const r = await getRanInventoryLedger(refresh)
      const payload = r.data as Ledger
      if (payload?.error) throw new Error(payload.error)
      setData(payload)
    } catch (e: any) {
      setError(e?.message || String(e))
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  /* ---- 前端筛选（与卡片、型号列、导出共用同一份 rows） ---- */
  const rows = useMemo(() => {
    const kw = q.trim().toLowerCase()
    return (data?.ne || []).filter(r => {
      if (moc && r.moc !== moc) return false
      if (oldOnly && !(r.age != null && r.age >= 8)) return false
      if (!kw) return true
      return `${r.site} ${r.display} ${r.ip} ${r.nbi} ${r.ver} ${r.sub}`.toLowerCase().includes(kw)
    })
  }, [data, q, moc, oldOnly])

  const sum = useMemo(() => ({
    hw: rows.reduce((s, r) => s + r.hw, 0),
    opt: rows.reduce((s, r) => s + r.opt, 0),
  }), [rows])

  const unitN = useCallback(
    (key: string) => data?.unit_totals?.find(u => u.key === key)?.n ?? 0,
    [data],
  )

  /* ---- 型号索引与列（筛选变化时重算，筛到 SDR 就不留一排 0 的板卡列） ---- */
  const defs = data?.model_defs || {}
  const modelIndex = useMemo(() => buildModelIndex(data?.ne || []), [data])
  const cats = useMemo(
    () => buildCatColumns(rows, modelIndex, defs, topN),
    [rows, modelIndex, defs, topN],
  )
  const visibleCats = useMemo(
    () => (cat === 'ALL' ? cats : cats.filter(c => c.cat === cat)),
    [cats, cat],
  )

  const columns = useMemo(
    () => (view === 'summary'
      ? buildSummaryColumns({ t, maps: modelIndex, defs })
      : buildPivotColumns({ t, maps: modelIndex, cats: visibleCats })),
    [view, t, modelIndex, defs, visibleCats],
  )

  const scrollX = useMemo(() => {
    if (view === 'summary') return 1800
    const groups = visibleCats.reduce(
      (s, c) => s + 80 + c.cols.length * 96 + (c.other > 0 ? 84 : 0), 0)
    return 410 + groups + 120
  }, [view, visibleCats])

  /* ---- 图表（配置见 ranInventory/ranInventoryCharts.ts） ---- */
  const charts = useMemo(
    () => buildCharts(data, isDark, colors, t),
    [data, isDark, colors, t],
  )

  /* ---- 导出（三套实现见 ranInventoryExport.ts，均走 UNIFIED_EXCEL_V1） ---- */
  const doExport = useCallback(() => {
    if (!rows.length) return
    const base = { t, rows, modelIndex, generatedAt: data?.generated_at || '' }
    if (view === 'summary') void exportLedger({ ...base, defs })
    else void exportPivot({ ...base, cats: visibleCats, topN })
  }, [rows, modelIndex, defs, visibleCats, data, t, view, topN])

  const doExportDetail = useCallback(() => {
    if (!drill || !detail) return
    void exportDetail({ t, ne: drill.ne, detail, generatedAt: data?.generated_at || '' })
  }, [drill, detail, data, t])

  /* ---- 下钻 ---- */
  const openDetail = useCallback(async (ne: NeRow, at?: { x: number; y: number }) => {
    setDrill({ ne, anchor: at })
    setDetail(null)
    setDetailLoading(true)
    try {
      const r = await getRanInventoryNe(ne.id)
      setDetail((r.data as Detail) || null)
    } catch {
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const c = data?.counts
  const drillNe = drill?.ne
  const topVendor = data?.opt_vendors?.[0]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>

      {/* 口径说明：默认收起（AGENTS.md 三.3b） */}
      <CalcChainNote
        title={t('ranInv.calc.title')}
        sections={[
          { title: t('ranInv.calc.online'), lines: [t('ranInv.calc.onlineLine')] },
          { title: t('ranInv.calc.join'), lines: [t('ranInv.calc.joinLine')] },
          { title: t('ranInv.calc.unit'), lines: [t('ranInv.calc.unitLine')] },
          { title: t('ranInv.calc.model'), lines: [t('ranInv.calc.modelLine')] },
          { title: t('ranInv.calc.age'), lines: [t('ranInv.calc.ageLine')] },
          { title: t('ranInv.calc.enum'), lines: [t('ranInv.calc.enumLine')] },
          { title: t('ranInv.calc.source'), lines: [t('ranInv.calc.sourceLine')] },
        ]}
      />

      {/* 工具条 */}
      <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
        <Space wrap>
          <Input
            allowClear
            placeholder={t('ranInv.search')}
            value={q}
            onChange={e => setQ(e.target.value)}
            style={{ width: 220 }}
          />
          <Select
            allowClear
            placeholder={t('ranInv.typeAll')}
            value={moc || undefined}
            onChange={v => setMoc(v || '')}
            options={(data?.by_type || []).map(b => ({ value: b.moc, label: `${b.moc} (${b.ne})` }))}
            style={{ width: 150 }}
          />
          <Space size={6}>
            <Switch size="small" checked={oldOnly} onChange={setOldOnly} />
            <Text style={{ fontSize: 12 }}>{t('ranInv.oldOnly')} ({c?.old_ne ?? 0})</Text>
          </Space>
          <Segmented
            className="wh-seg"
            value={view}
            onChange={v => setView(v as View)}
            options={[
              { label: t('ranInv.view.summary'), value: 'summary' },
              { label: t('ranInv.view.models'), value: 'models' },
            ]}
          />
          {view === 'models' && (
            <Segmented
              className="wh-seg"
              value={cat}
              onChange={v => setCat(v as 'ALL' | ModelCat)}
              options={[
                { label: t('ranInv.model.all'), value: 'ALL' },
                ...MODEL_CATS.map(c => ({ label: t(MODEL_CAT_LABEL[c]), value: c })),
              ]}
            />
          )}
          {view === 'models' && (
            <Space size={6}>
              <Text style={{ fontSize: 12 }}>{t('ranInv.model.topN')}</Text>
              <Select
                value={topN}
                onChange={setTopN}
                style={{ width: 76 }}
                options={TOPN_OPTIONS.map(n => ({ value: n, label: String(n) }))}
              />
            </Space>
          )}
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load(true)}>
            {t('ranInv.refresh')}
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => doExport()} disabled={!rows.length}>
            {t('common.exportExcel')}
          </Button>
          {data && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('ranInv.cacheHint', { snap: data.snapshot || '—', auth: data.auth_source || '—' })}
            </Text>
          )}
        </Space>
      </CardBase>

      {error && (
        <Alert
          type="error"
          showIcon
          message={t('ranInv.error')}
          description={error}
          action={<Button size="small" onClick={() => void load(true)}>{t('ranInv.retry')}</Button>}
        />
      )}

      {/* KPI 六卡 */}
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} lg={8} xxl={4}>
          <StatCard
            title={<>{t('ranInv.kpi.onlineNe')}<ExplainIcon text={t('ranInv.explain.onlineNe')} /></>}
            value={c?.ne_online ?? '—'}
            suffix={t('ranInv.unit.ne')}
            subText={t('ranInv.kpi.neSub', { total: c?.ne_total ?? 0, off: c?.ne_offline ?? 0 })}
            explainText={t('ranInv.tip.onlineNe', {
              on: c?.ne_online ?? 0, total: c?.ne_total ?? 0, off: c?.ne_offline ?? 0,
            })}
          />
        </Col>
        <Col xs={24} sm={12} lg={8} xxl={4}>
          <StatCard
            title={<>{t('ranInv.kpi.sites')}<ExplainIcon text={t('ranInv.explain.sites')} /></>}
            value={c?.ne_sites ?? '—'}
            suffix={t('ranInv.unit.site')}
            subText={t('ranInv.kpi.siteSub', { n: (c?.ne_online ?? 0) - (c?.ne_sites ?? 0) })}
            explainText={t('ranInv.tip.sites', { n: c?.ne_sites ?? 0 })}
            accentColor={WH.blue}
          />
        </Col>
        <Col xs={24} sm={12} lg={8} xxl={4}>
          <StatCard
            title={<>{t('ranInv.kpi.hw')}<ExplainIcon text={t('ranInv.explain.hw')} /></>}
            value={c?.hw_online ?? '—'}
            suffix={t('ranInv.unit.unit')}
            subText={t('ranInv.kpi.hwSub', {
              pack: unitN('pack'), rru: unitN('RRU'), ant: unitN('antenna'),
            })}
            explainText={t('ranInv.tip.hwCard', { n: c?.hw_online ?? 0, bad: c?.hw_bad ?? 0 })}
            accentColor={WH.teal}
          />
        </Col>
        <Col xs={24} sm={12} lg={8} xxl={4}>
          <StatCard
            title={<>{t('ranInv.kpi.sw')}<ExplainIcon text={t('ranInv.explain.sw')} /></>}
            value={c?.sw_online ?? '—'}
            suffix={t('ranInv.unit.pkg')}
            subText={t('ranInv.kpi.swSub', { p: c?.patch_total ?? 0, n: c?.patch_ne ?? 0 })}
            explainText={t('ranInv.tip.sw', { n: c?.sw_online ?? 0, p: c?.patch_total ?? 0 })}
            accentColor={WH.warn}
          />
        </Col>
        <Col xs={24} sm={12} lg={8} xxl={4}>
          <StatCard
            title={<>{t('ranInv.kpi.opt')}<ExplainIcon text={t('ranInv.explain.opt')} /></>}
            value={c?.opt_online ?? '—'}
            suffix={t('ranInv.unit.piece')}
            subText={t('ranInv.kpi.optSub', {
              vendor: topVendor?.name === '其他' ? t('ranInv.other') : (topVendor?.name ?? '—'),
              n: topVendor?.n ?? 0,
              pct: c?.opt_online ? (((topVendor?.n ?? 0) * 100) / c.opt_online).toFixed(1) : '0.0',
            })}
            explainText={t('ranInv.tip.opt', { n: c?.opt_online ?? 0, bad: c?.opt_bad ?? 0 })}
            accentColor={WH.indigo}
          />
        </Col>
        <Col xs={24} sm={12} lg={8} xxl={4}>
          <StatCard
            title={<>{t('ranInv.kpi.age')}<ExplainIcon text={t('ranInv.explain.age')} /></>}
            value={c?.avg_age == null ? '—' : c.avg_age}
            suffix={t('ranInv.unit.year')}
            subText={t('ranInv.kpi.ageSub', { n: c?.old_ne ?? 0 })}
            explainText={t('ranInv.tip.ageCard', { v: c?.avg_age == null ? '—' : c.avg_age, n: c?.old_ne ?? 0 })}
            accentColor={WH.danger}
          />
        </Col>
      </Row>

      {/* 分布图 */}
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={12} xxl={8}>
          <CardBase title={t('ranInv.chart.type')} extra={<Text type="secondary" style={{ fontSize: 12 }}>{t('ranInv.note.ne')}</Text>}>
            {data ? <ReactECharts option={charts.type} style={{ height: 240 }} notMerge /> : <Empty />}
          </CardBase>
        </Col>
        <Col xs={24} lg={12} xxl={8}>
          <CardBase title={t('ranInv.chart.unit')} extra={<Text type="secondary" style={{ fontSize: 12 }}>{t('ranInv.note.unit')}</Text>}>
            {data ? <ReactECharts option={charts.unit} style={{ height: 240 }} notMerge /> : <Empty />}
          </CardBase>
        </Col>
        <Col xs={24} lg={12} xxl={8}>
          <CardBase title={t('ranInv.chart.year')} extra={<Text type="secondary" style={{ fontSize: 12 }}>{t('ranInv.note.year')}</Text>}>
            {data ? <ReactECharts option={charts.year} style={{ height: 240 }} notMerge /> : <Empty />}
          </CardBase>
        </Col>
        <Col xs={24} lg={12}>
          <CardBase title={t('ranInv.chart.vendor')} extra={<Text type="secondary" style={{ fontSize: 12 }}>{t('ranInv.note.piece')}</Text>}>
            {data ? <ReactECharts option={charts.vendor} style={{ height: 260 }} notMerge /> : <Empty />}
          </CardBase>
        </Col>
        <Col xs={24} lg={12}>
          <CardBase title={t('ranInv.chart.model')} extra={<Text type="secondary" style={{ fontSize: 12 }}>{t('ranInv.note.block')}</Text>}>
            {data ? <ReactECharts option={charts.model} style={{ height: 260 }} notMerge /> : <Empty />}
          </CardBase>
        </Col>
      </Row>

      {/* 台账表 */}
      <CardBase
        title={t('ranInv.table.title')}
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            {view === 'models'
              ? t('ranInv.model.note', { n: topN })
              : t('ranInv.count', { n: rows.length, hw: sum.hw, opt: sum.opt })}
          </Text>
        }
      >
        <Table<NeRow>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={rows}
          loading={loading}
          scroll={{ x: scrollX }}
          pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (n) => t('ranInv.total', { n }) }}
          onRow={(r) => ({
            onClick: (e) => void openDetail(r, { x: e.clientX, y: e.clientY }),
            style: { cursor: 'pointer' },
          })}
          locale={{ emptyText: <Empty description={loading ? t('ranInv.loading') : t('ranInv.empty')} /> }}
        />
      </CardBase>

      {/* 网元资产明细 */}
      <DraggableClickModal
        open={!!drill}
        anchor={drill?.anchor || null}
        width="92%"
        maxWidth="72rem"
        title={t('ranInv.detail.title', { site: drillNe?.site || '—' })}
        onClose={() => { setDrill(null); setDetail(null) }}
        headerExtra={detail ? (
          <Button size="small" icon={<DownloadOutlined />} onClick={() => doExportDetail()}>
            {t('common.exportExcel')}
          </Button>
        ) : null}
      >
        {drillNe && (
          <RanInventoryDetail
            ne={drillNe}
            detail={detail}
            loading={detailLoading}
            unitTotals={data?.unit_totals || []}
          />
        )}
      </DraggableClickModal>
    </div>
  )
}
