/* ============================================================================
 * PRTG 核心链路健康度看板（?page=prtg_links，GitHub Issue #228）
 * ----------------------------------------------------------------------------
 * 数据源：GET /api/prtg/overview、/api/prtg/links、/api/prtg/links/{sensor_id}
 * 后端实现：backend/routers/prtg.py → backend/services/prtg/dashboard.py（只读）
 *
 * 口径（页面「数据口径说明」逐条对应，全部来自后端 docstring 与
 * docs/workspace/prtg-channel-dictionary.md）：
 *   - Traffic In / Out / Total 是速率，后端把原始 byte/s 换算成 Mbps 后下发；
 *   - Errors / Discards / Unicast 是 PRTG 给的**区间速率**（#/s），不差分；
 *   - 利用率 = max(In / Cin, Out / Cout)，没有容量时不算（显示「—」）；
 *   - 容量 PRTG 不提供，缺失显示「待补全」，不得按 0 参与计算；
 *   - 所有时间戳 UTC；数据新鲜度超过 5 分钟必须常驻提示（这是异常，不是口径解释）。
 *
 * 三条全站硬约束（AGENTS.md 三.3b / 3c）：
 *   ① 口径说明用 CalcChainNote，默认收起；
 *   ② 列头 ExplainIcon + 数字 ExplainPopover，气泡只引用接口字段，不重算；
 *   ③ 取不到数据一律「—」，不补 0。
 * ========================================================================== */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Col, Empty, Row, Segmented, Space, Spin, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ReloadOutlined } from '@ant-design/icons'

import { getPrtgLinkDetail, getPrtgLinks, getPrtgOverview } from '../api'
import { useI18n } from '../i18n'
import { useTheme, WH } from '../theme'
import { CardBase, StatCard } from '../components/cards'
import CalcChainNote from '../components/CalcChainNote'
import { ExplainIcon, ExplainPopover } from '../components/MetricExplain'
import DraggableClickModal from '../components/DraggableClickModal'
import { PrtgTrendChart } from './prtg/PrtgCharts'
import PrtgLinkDetail from './prtg/PrtgLinkDetail'
import PrtgSlaView from './prtg/PrtgSlaView'
import {
  explainCapacity, explainDiscards, explainErrors, explainIn, explainLastDown,
  explainOut, explainSite, explainStatus, explainTotal, explainUnicast,
  explainUptime, explainUtilization,
  fmtInt, fmtNum, fmtTime, matchLabelKey, NA, PRTG_WINDOWS,
  PrtgLink, PrtgLinkDetailData, PrtgOverview, PrtgWindow,
  statusColor, statusLabelKey, windowLabelKey,
} from './prtg/prtgModels'

const { Text } = Typography

/** 数据新鲜度阈值：超过 5 分钟按「已过期」常驻告警（与需求一致） */
const STALE_SECONDS = 300

export default function PrtgLinkHealth() {
  const { t } = useI18n()
  const { isDark } = useTheme()

  /* ---- 视图切换：链路健康 / SLA 与容量（#231） ---- */
  const [view, setView] = useState<'health' | 'sla'>('health')

  /* ---- 时间范围 ---- */
  const [windowKey, setWindowKey] = useState<PrtgWindow>('24h')

  /* ---- 数据与状态 ---- */
  const [overview, setOverview] = useState<PrtgOverview | null>(null)
  const [links, setLinks] = useState<PrtgLink[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  /* ---- 详情抽屉 ---- */
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailId, setDetailId] = useState<number | null>(null)
  const [detail, setDetail] = useState<PrtgLinkDetailData | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [anchor, setAnchor] = useState<{ x: number; y: number } | null>(null)

  /* ---- 取数：overview 与 links 一起拉；任一失败都落到 error（不静默） ---- */
  const load = useCallback(async () => {
    setLoading(true)
    setError('')
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

  /* ---- 详情：点行后按当前时间窗取；窗口变化时重新取一次 ---- */
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
    setAnchor(at || null)
    setDetailLoading(true)
    setDetailError('')
    setDetailOpen(true)
  }, [])

  /* ---- KPI 数字：没有 overview 时一律「—」，不显示 0 ---- */
  const kpi = overview?.kpi
  const age = overview?.data_age_seconds
  const stale = age != null && age > STALE_SECONDS
  const ageText = overview == null
    ? NA
    : age == null ? t('prtg.freshness.missing') : t('prtg.freshness.seconds', { n: age })

  const kpiCards = useMemo<Array<{
    key: string; title: string; value: string; suffix?: string; explain: string; color: string
  }>>(() => {
    const has = overview != null
    return [
      {
        key: 'total',
        title: t('prtg.kpi.total'),
        value: has ? fmtInt(kpi?.total) : NA,
        explain: t('prtg.explain.total'),
        color: WH.teal,
      },
      {
        key: 'up',
        title: t('prtg.kpi.up'),
        value: has ? fmtInt(kpi?.up) : NA,
        explain: t('prtg.explain.up'),
        color: WH.ok,
      },
      {
        key: 'unusual',
        title: t('prtg.kpi.unusual'),
        value: has ? fmtInt(kpi?.unusual) : NA,
        explain: t('prtg.explain.unusual'),
        color: WH.warn,
      },
      {
        key: 'down',
        title: t('prtg.kpi.down'),
        value: has ? fmtInt(kpi?.down) : NA,
        explain: t('prtg.explain.down'),
        color: WH.danger,
      },
      {
        key: 'avgUptime',
        title: t('prtg.kpi.avgUptime'),
        value: has ? fmtNum(kpi?.avg_uptime_pct ?? null, 3) : NA,
        suffix: has && kpi?.avg_uptime_pct != null ? '%' : '',
        explain: t('prtg.explain.avgUptime', { n: kpi?.uptime_samples ?? NA }),
        color: WH.teal,
      },
      {
        key: 'anomaly',
        title: t('prtg.kpi.anomaly'),
        value: has ? fmtInt(kpi?.anomaly_links) : NA,
        explain: t('prtg.explain.anomaly'),
        color: (kpi?.anomaly_links ?? 0) > 0 ? WH.warn : WH.ok,
      },
    ]
  }, [overview, kpi, t])

  /* ---- 单元格渲染：所有数字都包 ExplainPopover，气泡内容来自接口字段 ---- */
  const numCell = useCallback((
    v: number | null | undefined, explain: string, digits = 2, suffix = '',
  ) => (
    <ExplainPopover text={explain} placement="left">
      <span style={{ fontVariantNumeric: 'tabular-nums' }}>
        {v == null ? NA : `${fmtNum(v, digits)}${suffix}`}
      </span>
    </ExplainPopover>
  ), [])

  const pairCell = useCallback((
    a: number | null | undefined, b: number | null | undefined, explain: string,
  ) => (
    <ExplainPopover text={explain} placement="left">
      <span style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtNum(a)} / {fmtNum(b)}</span>
    </ExplainPopover>
  ), [])

  /* ---- 主表列：列头 ⓘ 讲这一列，数字本身悬浮讲这一格 ---- */
  const columns = useMemo<ColumnsType<PrtgLink>>(() => [
    {
      title: <>{t('prtg.col.status')} <ExplainIcon text={t('prtg.colExplain.status')} /></>,
      dataIndex: 'status',
      width: 100,
      render: (_: unknown, link: PrtgLink) => (
        <ExplainPopover text={explainStatus(link, t)}>
          <Tag color={statusColor(link.status)}>{t(statusLabelKey(link.status))}</Tag>
        </ExplainPopover>
      ),
    },
    {
      title: <>{t('prtg.col.link')} <ExplainIcon text={t('prtg.colExplain.link')} /></>,
      key: 'link',
      width: 250,
      render: (_: unknown, link: PrtgLink) => (
        <div>
          <div style={{ color: WH.text }}>{link.device_name || NA}</div>
          <div style={{ fontSize: 12, color: WH.textMuted }}>{link.sensor_name}</div>
        </div>
      ),
    },
    {
      title: <>{t('prtg.col.site')} <ExplainIcon text={t('prtg.colExplain.site')} /></>,
      key: 'site',
      width: 190,
      render: (_: unknown, link: PrtgLink) => (
        <ExplainPopover text={explainSite(link, t)}>
          <div>
            <div>{link.site_id && link.site_id !== 'Unknown' ? link.site_id : NA}</div>
            <div style={{ fontSize: 12, color: WH.textMuted }}>
              {link.region || NA} · {t(matchLabelKey(link.match_status))}
            </div>
          </div>
        </ExplainPopover>
      ),
    },
    {
      title: <>{t('prtg.col.in')} <ExplainIcon text={t('prtg.colExplain.in')} /></>,
      key: 'in',
      width: 110,
      align: 'right',
      render: (_: unknown, link: PrtgLink) => numCell(link.in_mbps, explainIn(link, t)),
    },
    {
      title: <>{t('prtg.col.out')} <ExplainIcon text={t('prtg.colExplain.out')} /></>,
      key: 'out',
      width: 110,
      align: 'right',
      render: (_: unknown, link: PrtgLink) => numCell(link.out_mbps, explainOut(link, t)),
    },
    {
      title: <>{t('prtg.col.total')} <ExplainIcon text={t('prtg.colExplain.total')} /></>,
      key: 'total',
      width: 120,
      align: 'right',
      render: (_: unknown, link: PrtgLink) => numCell(link.total_mbps, explainTotal(link, t)),
    },
    {
      title: <>{t('prtg.col.unicast')} <ExplainIcon text={t('prtg.colExplain.unicast')} /></>,
      key: 'unicast',
      width: 130,
      align: 'right',
      render: (_: unknown, link: PrtgLink) => pairCell(link.unicast_in, link.unicast_out, explainUnicast(link, t)),
    },
    {
      title: <>{t('prtg.col.errors')} <ExplainIcon text={t('prtg.colExplain.errors')} /></>,
      key: 'errors',
      width: 120,
      align: 'right',
      render: (_: unknown, link: PrtgLink) => pairCell(link.errors_in, link.errors_out, explainErrors(link, t)),
    },
    {
      title: <>{t('prtg.col.discards')} <ExplainIcon text={t('prtg.colExplain.discards')} /></>,
      key: 'discards',
      width: 120,
      align: 'right',
      render: (_: unknown, link: PrtgLink) => pairCell(link.discards_in, link.discards_out, explainDiscards(link, t)),
    },
    {
      title: <>{t('prtg.col.uptime')} <ExplainIcon text={t('prtg.colExplain.uptime')} /></>,
      key: 'uptime',
      width: 110,
      align: 'right',
      render: (_: unknown, link: PrtgLink) => numCell(link.uptime_pct, explainUptime(link, t), 3, '%'),
    },
    {
      title: <>{t('prtg.col.lastDown')} <ExplainIcon text={t('prtg.colExplain.lastDown')} /></>,
      dataIndex: 'last_down',
      width: 170,
      render: (_: unknown, link: PrtgLink) => (
        <ExplainPopover text={explainLastDown(link, t)} placement="left">
          <span>{fmtTime(link.last_down)}</span>
        </ExplainPopover>
      ),
    },
    {
      title: <>{t('prtg.col.capacity')} <ExplainIcon text={t('prtg.colExplain.capacity')} /></>,
      key: 'capacity',
      width: 120,
      align: 'right',
      render: (_: unknown, link: PrtgLink) => (
        <ExplainPopover text={explainCapacity(link, t)} placement="left">
          <span style={{ color: link.capacity_mbps == null ? WH.warn : undefined }}>
            {link.capacity_mbps == null
              ? t('prtg.capacityMissing')
              : `${fmtNum(link.capacity_mbps)} ${t('prtg.trend.unit')}`}
          </span>
        </ExplainPopover>
      ),
    },
    {
      title: <>{t('prtg.col.utilization')} <ExplainIcon text={t('prtg.colExplain.utilization')} /></>,
      key: 'utilization',
      width: 110,
      align: 'right',
      render: (_: unknown, link: PrtgLink) => numCell(link.utilization_pct, explainUtilization(link, t), 2, '%'),
    },
  ], [t, numCell, pairCell])

  const selectedLink = useMemo(
    () => links.find(l => l.sensor_id === detailId),
    [links, detailId],
  )
  const modalTitle = detail
    ? `${detail.device_name || NA} · ${detail.sensor_name}`
    : selectedLink
      ? `${selectedLink.device_name || NA} · ${selectedLink.sensor_name}`
      : t('prtg.detail.title')

  if (view === 'sla') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>
        <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
          <Segmented
            value={view}
            onChange={v => setView(v as 'health' | 'sla')}
            options={[
              { value: 'health', label: t('prtg.tab.health') },
              { value: 'sla', label: t('prtg.tab.sla') },
            ]}
          />
        </CardBase>
        <PrtgSlaView />
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>

      <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
        <Segmented
          value={view}
          onChange={v => setView(v as 'health' | 'sla')}
          options={[
            { value: 'health', label: t('prtg.tab.health') },
            { value: 'sla', label: t('prtg.tab.sla') },
          ]}
        />
      </CardBase>

      {/* ① 筛选条：时间范围 + 刷新 + 生成时间 */}
      <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
        <Space wrap>
          <Text type="secondary">{t('prtg.window.label')}</Text>
          <Segmented
            value={windowKey}
            onChange={v => setWindowKey(v as PrtgWindow)}
            options={PRTG_WINDOWS.map(w => ({ value: w, label: t(windowLabelKey(w)) }))}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            {t('common.refresh')}
          </Button>
          <Tag>{t('prtg.freshness.label')}: {ageText}</Tag>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('prtg.generatedAt', { time: fmtTime(overview?.generated_at) })}
          </Text>
        </Space>
      </CardBase>

      {/* ② 错误 / 过期 / 采集器异常：状态反馈必须常驻（不是「解释」） */}
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

      {/* ③ 口径说明：默认收起（AGENTS.md 三.3b） */}
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

      {/* ④ KPI 数字卡：标题 ⓘ + 数字悬浮都由 StatCard 统一承载 */}
      <Row gutter={[12, 12]}>
        {kpiCards.map(card => (
          <Col xs={12} sm={8} lg={4} key={card.key}>
            <StatCard
              title={(
                <span>
                  {card.title}
                  <span style={{ marginLeft: 4 }}>
                    <ExplainIcon text={card.explain} />
                  </span>
                </span>
              )}
              value={card.value}
              suffix={card.suffix}
              explainText={card.explain}
              accentColor={card.color}
            />
          </Col>
        ))}
      </Row>

      {/* ⑤ 全网速率趋势：自绘 SVG，不嵌 chart.png */}
      <CardBase
        title={<>{t('prtg.trend.title')} <ExplainIcon text={t('prtg.trend.explain')} /></>}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>{t(windowLabelKey(windowKey))}</Text>}
      >
        {overview
          ? <PrtgTrendChart points={overview.trend || []} windowKey={windowKey} isDark={isDark} />
          : <Empty description={t('prtg.trend.empty')} />}
      </CardBase>

      {/* ⑥ 25 条链路表：点任意行开详情 */}
      <CardBase
        title={<>{t('prtg.table.title')} <ExplainIcon text={t('prtg.table.hint')} /></>}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>{overview == null ? NA : `${links.length} / 25`}</Text>}
      >
        <Table<PrtgLink>
          rowKey="sensor_id"
          columns={columns}
          dataSource={links}
          loading={loading}
          pagination={false}
          size="small"
          scroll={{ x: 1750 }}
          onRow={link => ({
            onClick: e => openDetail(link, { x: e.clientX, y: e.clientY }),
            style: { cursor: 'pointer' },
          })}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={error ? t('prtg.warn.loadFailed') : t('prtg.empty')}
              />
            ),
          }}
        />
      </CardBase>

      {/* ⑦ 详情弹窗：传感器信息 + 映射 + 事件 + In/Out/Total 时序 */}
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
