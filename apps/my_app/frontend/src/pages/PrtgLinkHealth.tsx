/* ============================================================================
 * PRTG 核心链路健康度看板（?page=prtg_links，GitHub Issue #228）
 * ----------------------------------------------------------------------------
 * 数据源：GET /api/prtg/overview、/api/prtg/links、/api/prtg/links/{sensor_id}
 * 后端实现：backend/routers/prtg.py → backend/services/prtg/dashboard.py（只读）
 *
 * 改版 P1/P2（前端）：顶栏合并工具条 · 4 KPI · 拓扑/趋势可切换槽 + 关注面板 ·
 * 7 列分组表 · 名称清洗 · Incidents/CalcChainNote 下沉。
 *
 * 三条全站硬约束（AGENTS.md 三.3b / 3c）：
 *   ① 口径说明用 CalcChainNote，默认收起；
 *   ② 列头 ExplainIcon + 数字 ExplainPopover，气泡只引用接口字段，不重算；
 *   ③ 取不到数据一律「—」，不补 0。
 * ========================================================================== */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Alert, Button, Empty, Segmented, Space, Spin, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

import { getPrtgLinkDetail, getPrtgLinks, getPrtgOverview } from '../api'
import { useI18n } from '../i18n'
import { useTheme, WH } from '../theme'
import { CardBase } from '../components/cards'
import CalcChainNote from '../components/CalcChainNote'
import { ExplainIcon } from '../components/MetricExplain'
import DraggableClickModal from '../components/DraggableClickModal'
import { PrtgTrendChart } from './prtg/PrtgCharts'
import PrtgLinkDetail from './prtg/PrtgLinkDetail'
import PrtgSlaView from './prtg/PrtgSlaView'
import PrtgIncidents from './prtg/PrtgIncidents'
import PrtgAttentionPanel from './prtg/PrtgAttentionPanel'
import PrtgHealthKpis from './prtg/PrtgHealthKpis'
import PrtgLinksTable from './prtg/PrtgLinksTable'
import PrtgTopologyView, { PrtgVisualModeSwitch } from './prtg/PrtgTopologyView'
import {
  NA, PRTG_WINDOWS, PrtgLink, PrtgLinkDetailData, PrtgOverview, PrtgWindow,
  cleanSensorName, fmtTime, shortDeviceName, windowLabelKey,
} from './prtg/prtgModels'

const { Text } = Typography

/** 数据新鲜度阈值：超过 5 分钟按「已过期」常驻告警 */
const STALE_SECONDS = 300

/**
 * 左侧主视觉槽位：默认拓扑；可切回全网趋势。右侧关注面板独立。
 */
function OverviewVisualSlot({ children }: { children: ReactNode }) {
  return (
    <div data-prtg-slot="overview-visual" style={{ flex: '1 1 60%', minWidth: 0 }}>
      {children}
    </div>
  )
}

export default function PrtgLinkHealth() {
  const { t } = useI18n()
  const { isDark } = useTheme()

  const [view, setView] = useState<'health' | 'sla'>('health')
  const [windowKey, setWindowKey] = useState<PrtgWindow>('24h')

  const [overview, setOverview] = useState<PrtgOverview | null>(null)
  const [links, setLinks] = useState<PrtgLink[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [refreshToken, setRefreshToken] = useState(0)

  const [detailOpen, setDetailOpen] = useState(false)
  const [detailId, setDetailId] = useState<number | null>(null)
  const [detail, setDetail] = useState<PrtgLinkDetailData | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [anchor, setAnchor] = useState<{ x: number; y: number } | null>(null)

  /* 槽位：拓扑（默认）/ 趋势；关注面板悬停高亮拓扑边 */
  const [visualMode, setVisualMode] = useState<'topo' | 'trend'>('topo')
  const [highlightSensorId, setHighlightSensorId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    setRefreshToken(n => n + 1)
    try {
      const [overviewRes, linksRes] = await Promise.all([
        getPrtgOverview({ window: windowKey }),
        getPrtgLinks({ window: windowKey }),
      ])
      setOverview(overviewRes.data || null)
      setLinks(Array.isArray(linksRes.data?.links) ? linksRes.data.links : [])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
      setOverview(null)
      setLinks([])
    } finally {
      setLoading(false)
    }
  }, [windowKey])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!detailOpen || detailId == null) return
    let cancelled = false
    setDetailLoading(true)
    setDetailError('')
    setDetail(null)
    getPrtgLinkDetail(detailId, { window: windowKey })
      .then(res => { if (!cancelled) setDetail(res.data || null) })
      .catch((e: unknown) => {
        if (!cancelled) setDetailError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => { if (!cancelled) setDetailLoading(false) })
    return () => { cancelled = true }
  }, [detailOpen, detailId, windowKey])

  const openDetail = useCallback((link: PrtgLink, at?: { x: number; y: number }) => {
    setDetailId(link.sensor_id)
    setHighlightSensorId(link.sensor_id)
    setAnchor(at || null)
    setDetailLoading(true)
    setDetailError('')
    setDetailOpen(true)
  }, [])

  const kpi = overview?.kpi
  const age = overview?.data_age_seconds
  const stale = age != null && age > STALE_SECONDS
  const ageText = overview == null
    ? NA
    : age == null ? t('prtg.freshness.missing') : t('prtg.freshness.seconds', { n: age })

  const freshnessDot = overview == null
    ? WH.textMuted
    : stale || age == null
      ? WH.danger
      : WH.ok

  const selectedLink = useMemo(
    () => links.find(l => l.sensor_id === detailId),
    [links, detailId],
  )

  const modalTitle = useMemo(() => {
    const device = detail?.device_name ?? selectedLink?.device_name
    const sensor = detail?.sensor_name ?? selectedLink?.sensor_name
    if (device || sensor) {
      return `${shortDeviceName(device)} · ${cleanSensorName(sensor)}`
    }
    return t('prtg.detail.title')
  }, [detail, selectedLink, t])

  const viewSwitcher = (
    <Segmented
      className="wh-seg"
      value={view}
      onChange={v => setView(v as 'health' | 'sla')}
      options={[
        { value: 'health', label: t('prtg.tab.health') },
        { value: 'sla', label: t('prtg.tab.sla') },
      ]}
    />
  )

  if (view === 'sla') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>
        <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
          {viewSwitcher}
        </CardBase>
        <PrtgSlaView />
      </div>
    )
  }

  const trendCard = (
    <CardBase
      title={<>{t('prtg.trend.title')} <ExplainIcon text={t('prtg.trend.explain')} /></>}
      extra={<Text type="secondary" style={{ fontSize: 12 }}>{t(windowLabelKey(windowKey))}</Text>}
    >
      {overview
        ? <PrtgTrendChart points={overview.trend || []} windowKey={windowKey} isDark={isDark} />
        : <Empty description={t('prtg.trend.empty')} />}
    </CardBase>
  )

  const visualSlot = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <PrtgVisualModeSwitch mode={visualMode} onChange={setVisualMode} />
      </div>
      {visualMode === 'topo' ? (
        <PrtgTopologyView
          windowKey={windowKey}
          pageLinks={links}
          isDark={isDark}
          highlightSensorId={highlightSensorId}
          onOpenDetail={openDetail}
          onSwitchToTrend={() => setVisualMode('trend')}
        />
      ) : trendCard}
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>

      {/* ① 合并工具栏：视图 + 时间窗 + 刷新 + 新鲜度 + 生成时间 */}
      <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
        <Space wrap size="middle" style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            {viewSwitcher}
            <Text type="secondary">{t('prtg.window.label')}</Text>
            <Segmented
              className="wh-seg"
              value={windowKey}
              onChange={v => setWindowKey(v as PrtgWindow)}
              options={PRTG_WINDOWS.map(w => ({ value: w, label: t(windowLabelKey(w)) }))}
            />
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
              {t('common.refresh')}
            </Button>
          </Space>
          <Space wrap size="middle">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <span
                aria-hidden
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: freshnessDot,
                  boxShadow: stale ? `0 0 0 3px color-mix(in srgb, ${WH.danger} 25%, transparent)` : undefined,
                }}
              />
              <Text type="secondary">{t('prtg.freshness.label')}:</Text>
              <Text style={{ color: stale ? WH.danger : WH.text }}>{ageText}</Text>
            </span>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('prtg.generatedAt', { time: fmtTime(overview?.generated_at) })}
            </Text>
          </Space>
        </Space>
      </CardBase>

      {/* ② 错误 / 过期 / 采集器异常：状态反馈必须常驻 */}
      {error && (
        <Alert type="error" showIcon message={t('prtg.warn.loadFailed')} description={error} />
      )}
      {stale && (
        <Alert
          type="warning"
          showIcon
          message={t('prtg.freshness.staleTitle', { n: age ?? NA })}
          description={t('prtg.freshness.staleBody', {
            time: fmtTime(overview?.collector?.last_success_at),
            n: age ?? NA,
          })}
        />
      )}
      {overview?.collector?.error && (
        <Alert
          type="error"
          showIcon
          message={t('prtg.collector.errorTitle')}
          description={overview.collector.error}
        />
      )}

      {/* ③ 4 张 KPI */}
      <PrtgHealthKpis kpi={kpi} links={links} hasOverview={overview != null} />

      {/* ④ 趋势（可替换槽）≈60% + 需要关注 ≈40%；窄屏堆叠 */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--wh-space-md)',
          alignItems: 'stretch',
        }}
      >
        <OverviewVisualSlot>{visualSlot}</OverviewVisualSlot>
        <div style={{ flex: '1 1 36%', minWidth: 260 }}>
          <PrtgAttentionPanel
            links={links}
            onSelect={openDetail}
            onHighlight={setHighlightSensorId}
          />
        </div>
      </div>

      {/* ⑤ 7 列分组表 */}
      <PrtgLinksTable
        links={links}
        loading={loading}
        error={error}
        onOpenDetail={openDetail}
      />

      {/* ⑥ 次要内容下沉：设备事件 + 口径说明（默认收起） */}
      <PrtgIncidents refreshToken={refreshToken} />
      <CalcChainNote
        title={t('prtg.note.title')}
        sections={[
          { title: t('prtg.note.sourceTitle'), lines: [t('prtg.note.source')] },
          { title: t('prtg.note.pollTitle'), lines: [t('prtg.note.poll')] },
          { title: t('prtg.note.rateTitle'), lines: [t('prtg.note.rate')] },
          { title: t('prtg.note.utilTitle'), lines: [t('prtg.note.util')] },
          { title: t('prtg.note.tzTitle'), lines: [t('prtg.note.tz')] },
          { title: t('prtg.note.qualityTitle'), lines: [t('prtg.note.quality')] },
        ]}
      />

      {/* ⑦ 详情弹窗：内容不改，仅标题用清洗名 */}
      <DraggableClickModal
        open={detailOpen}
        title={modalTitle}
        anchor={anchor}
        onClose={() => setDetailOpen(false)}
        width="92%"
        maxWidth="75rem"
      >
        {detailLoading && (
          <div style={{ padding: 'var(--wh-space-lg)', textAlign: 'center' }}>
            <Spin tip={t('common.loading')}>
              <div style={{ height: 60 }} />
            </Spin>
          </div>
        )}
        {!detailLoading && detailError && (
          <Alert type="error" showIcon message={t('prtg.detail.error')} description={detailError} />
        )}
        {!detailLoading && !detailError && detail && (
          <PrtgLinkDetail detail={detail} windowKey={windowKey} isDark={isDark} />
        )}
        {!detailLoading && !detailError && !detail && (
          <Empty description={t('prtg.detail.noSeries')} />
        )}
      </DraggableClickModal>
    </div>
  )
}
