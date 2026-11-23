/* ============================================================================
 * location/LocationPanels —— 位置页右栏的三张列表（站点 / 链路 / 订阅者轨迹）
 * ============================================================================
 *
 * 从 pages/LocationMap.tsx 拆出（该页原本 745 行，超过 AGENTS 三.2 的 500 行上限）。
 * 只做**渲染**：数据、选中态、抽屉开关都在页面持有；这里不取数、不写 URL。
 *
 * 三张表的分工：
 *   · SitePanel       最近一圈站点：色点与 GIS 同源、点行开 GIS 下钻抽屉
 *   · LinksPanel      最近 MW + 光纤链路：点行开各自的详情抽屉
 *   · SubscriberPanel 订阅者号码输入 + 驻留小区列表：点行开 VMAX 诊断抽屉
 * ========================================================================== */
import { Button, Empty, Input, Spin, Table, Tag, Typography } from 'antd'
import { SearchOutlined } from '@ant-design/icons'

import { useI18n } from '../../i18n'
import { useIsMobile } from '../../theme'
import { CardBase } from '../../components/cards'
import { ExplainIcon } from '../../components/MetricExplain'
import { UTIL_LEVEL_COLORS, utilLevelOf, type Site, type TopologyLink } from '../gismap/types'
import { fmtKm } from './geo'
import type { FiberLink } from './useLocationData'
import type { RingSite, TrackCell } from './LocationMapView'
import { siteColor } from './siteColors'

const { Text } = Typography

/* ------------------------------ 最近一圈站点 ------------------------------ */

export function SitePanel({
  items, radiusKm, onOpen,
}: {
  items: RingSite[]
  radiusKm: number
  onOpen: (site: Site) => void
}) {
  const { t } = useI18n()
  const isMobile = useIsMobile()
  const rows = items.map(({ site, km }) => ({ key: site.site_id, site, km }))
  const columns = [
    {
      title: t('loc.col.site'), dataIndex: 'site', ellipsis: true,
      render: (s: Site) => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          <span style={{
            width: 9, height: 9, borderRadius: '50%', flex: '0 0 auto',
            background: siteColor(s),
          }} />
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.site_name}</span>
        </span>
      ),
    },
    {
      key: 'km',
      title: <span>{t('loc.col.distance')}<ExplainIcon text={t('loc.explain.distance')} size="0.7rem" /></span>,
      dataIndex: 'km', width: isMobile ? 78 : 96,
      sorter: (a: { km: number }, b: { km: number }) => a.km - b.km,
      render: (km: number) => fmtKm(km),
    },
    {
      key: 'area',
      title: t('loc.col.area'), dataIndex: 'site', width: 110, ellipsis: true,
      render: (s: Site) => s.province_county || s.region || '—',
    },
    {
      key: 'alarms',
      title: t('loc.col.alarms'), dataIndex: 'site', width: isMobile ? 80 : 110,
      render: (s: Site) => (
        <span style={{ display: 'inline-flex', gap: 4 }}>
          {s.rt_alarm_count > 0 && <Tag color="red">{s.rt_alarm_count}</Tag>}
          {s.his_alarm_count > 0 && <Tag>{s.his_alarm_count}</Tag>}
          {!s.rt_alarm_count && !s.his_alarm_count && <Text type="secondary">—</Text>}
        </span>
      ),
    },
  ].filter((c) => !(isMobile && c.key === 'area'))

  return (
    <CardBase
      title={<span>{t('loc.list.sitesTitle', { n: items.length })}</span>}
      extra={<Text type="secondary" style={{ fontSize: 11 }}>{t('loc.list.ringRadius', { r: fmtKm(radiusKm) })}</Text>}
      bodyStyle={{ padding: 0 }}
    >
      {items.length ? (
        <Table
          size="small"
          columns={columns as any}
          dataSource={rows}
          pagination={false}
          scroll={{ y: 240 }}
          onRow={(row) => ({ onClick: () => onOpen(row.site), style: { cursor: 'pointer' } })}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">{t('loc.list.noSites')}</Text>} />
      )}
    </CardBase>
  )
}

/* ------------------------------ 最近的链路 ------------------------------ */

export function LinksPanel({
  mw, fiber, loadingMw, loadingFiber, onOpenMw, onOpenFiber,
}: {
  mw: { link: TopologyLink; km: number }[]
  fiber: { link: FiberLink; km: number }[]
  loadingMw: boolean
  loadingFiber: boolean
  onOpenMw: (link: TopologyLink) => void
  onOpenFiber: (link: FiberLink) => void
}) {
  const { t } = useI18n()
  const isMobile = useIsMobile()

  const mwColumns = [
    {
      title: t('loc.col.link'), dataIndex: 'link', ellipsis: true,
      render: (l: TopologyLink) => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          <span style={{
            width: 10, height: 3, borderRadius: 2, flex: '0 0 auto',
            background: UTIL_LEVEL_COLORS[l.level ?? utilLevelOf(l.util)],
          }} />
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {l.name || `${l.site_a_name}-${l.site_b_name}`}
          </span>
        </span>
      ),
    },
    {
      key: 'util',
      title: t('loc.col.util'), dataIndex: 'link', width: isMobile ? 70 : 84,
      render: (l: TopologyLink) => (
        l.util == null ? <Text type="secondary">{t('loc.list.noKpi')}</Text>
          : <span style={{ color: l.level === 'critical' ? UTIL_LEVEL_COLORS.critical : undefined }}>{Number(l.util).toFixed(2)}%</span>
      ),
    },
    {
      key: 'km',
      title: t('loc.col.distance'), dataIndex: 'km', width: isMobile ? 74 : 90,
      render: (km: number) => fmtKm(km),
    },
  ]

  const fiberColumns = [
    {
      title: t('loc.col.link'), dataIndex: 'link', ellipsis: true,
      render: (f: FiberLink) => (
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.aName} ↔ {f.bName}</span>
      ),
    },
    {
      key: 'type',
      title: t('loc.col.type'), dataIndex: 'link', width: 120, ellipsis: true,
      render: (f: FiberLink) => f.type || '—',
    },
    {
      key: 'km',
      title: t('loc.col.distance'), dataIndex: 'km', width: isMobile ? 74 : 90,
      render: (km: number) => fmtKm(km),
    },
  ].filter((c) => !(isMobile && c.key === 'type'))

  return (
    <CardBase
      title={<span>{t('loc.list.mwTitle')}<ExplainIcon text={t('loc.explain.mw')} size="0.75rem" /></span>}
      extra={<Text type="secondary" style={{ fontSize: 11 }}>{t('loc.list.clickHint')}</Text>}
      bodyStyle={{ padding: 0 }}
    >
      {loadingMw && !mw.length ? (
        <div style={{ padding: 16, textAlign: 'center' }}><Spin size="small" /></div>
      ) : mw.length ? (
        <Table
          size="small"
          columns={mwColumns as any}
          dataSource={mw.map((x) => ({ key: x.link.name || `${x.link.site_a_name}-${x.link.site_b_name}`, ...x }))}
          pagination={false}
          onRow={(row: any) => ({ onClick: () => onOpenMw(row.link), style: { cursor: 'pointer' } })}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">{t('loc.list.noLinks')}</Text>} />
      )}
      <div style={{ padding: '8px 12px', borderTop: '1px solid rgba(128,128,128,0.18)' }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>{t('loc.list.fiberTitle')}</div>
        {fiber.length ? (
          <Table
            size="small"
            columns={fiberColumns as any}
            dataSource={fiber.map((x) => ({ key: x.link.id, ...x }))}
            pagination={false}
            onRow={(row: any) => ({ onClick: () => onOpenFiber(row.link), style: { cursor: 'pointer' } })}
          />
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {loadingFiber ? t('loc.list.loadingFiber') : t('loc.list.noFiber')}
          </Text>
        )}
      </div>
    </CardBase>
  )
}

/* ------------------------------ 订阅者轨迹 ------------------------------ */

export function SubscriberPanel({
  msisdn, onMsisdnChange, searched, loading, diagLoading, onSearch,
  trackKm, missing, resultMsisdn, hasResult, onOpen,
}: {
  msisdn: string
  onMsisdnChange: (v: string) => void
  searched: boolean
  loading: boolean
  diagLoading: boolean
  onSearch: () => void
  trackKm: { cell: TrackCell; km: number }[]
  missing: number
  resultMsisdn?: string
  /** 本次查询是否拿到了结果（即使没有带坐标的小区，也应能打开完整诊断） */
  hasResult: boolean
  onOpen: () => void
}) {
  const { t } = useI18n()
  const isMobile = useIsMobile()
  const lastKey = trackKm[trackKm.length - 1]?.cell.key
  /** CCA 的 rat 数值 → 制式（1/2/6 是项目里实测到的三档；其它原样显示）。 */
  const ratText = (rat: number | string | undefined): string => {
    const s = String(rat ?? '')
    if (s === '1') return '2G'
    if (s === '2') return '3G'
    if (s === '6') return '4G'
    return s || '—'
  }
  const columns = [
    {
      key: 'idx', title: '#', width: 44,
      render: (_: unknown, row: { cell: TrackCell }, i: number) => (
        row.cell.key === lastKey
          ? <span title={t('loc.sub.lastHint')}>👤</span>
          : i + 1
      ),
    },
    {
      key: 'cell',
      title: t('loc.col.cell'), dataIndex: 'cell', ellipsis: true,
      render: (c: TrackCell) => c.name,
    },
    {
      key: 'vendor',
      title: t('loc.col.vendor'), dataIndex: 'cell', width: isMobile ? 70 : 96,
      render: (c: TrackCell) => c.vendor || '—',
    },
    {
      key: 'rat',
      title: t('loc.col.rat'), dataIndex: 'cell', width: 72,
      render: (c: TrackCell) => ratText(c.rat),
    },
    {
      key: 'coords',
      title: t('loc.col.coords'), dataIndex: 'cell', width: 190,
      render: (c: TrackCell) => (
        <span style={{ fontFamily: 'Consolas, monospace', fontSize: 12 }}>
          {c.lat.toFixed(5)}, {c.lon.toFixed(5)}
        </span>
      ),
    },
    {
      key: 'km',
      title: t('loc.col.distance'), dataIndex: 'km', width: 90,
      render: (km: number) => fmtKm(km),
    },
  ].filter((c) => !(isMobile && (c.key === 'coords' || c.key === 'vendor')))

  return (
    <CardBase
      title={<span>{t('loc.sub.cardTitle')}</span>}
      extra={resultMsisdn ? <Tag color="cyan">{resultMsisdn}</Tag> : undefined}
      bodyStyle={{ padding: 12 }}
    >
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
        <Input
          prefix={<SearchOutlined style={{ color: 'var(--wh-text-dim)' }} />}
          value={msisdn}
          onChange={(e) => onMsisdnChange(e.target.value)}
          onPressEnter={onSearch}
          placeholder={t('loc.sub.inputPlaceholder')}
          style={{ width: 200 }}
          maxLength={24}
          allowClear
        />
        <Button type="primary" loading={loading || diagLoading} onClick={onSearch}>
          {t('loc.sub.search')}
        </Button>
        <ExplainIcon text={t('loc.sub.explain')} size="0.85rem" />
      </div>
      {searched && (loading || diagLoading) && (
        <div style={{ padding: 8 }}>
          <Spin size="small" /> <Text type="secondary" style={{ fontSize: 12 }}>{t('loc.sub.loading')}</Text>
        </div>
      )}
      {searched && !loading && !diagLoading && (
        trackKm.length ? (
          <>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('loc.sub.cells', { n: trackKm.length })}
              {missing > 0 ? ` · ${t('loc.sub.missingCoords', { n: missing })}` : ''}
            </Text>
            <Table
              size="small"
              style={{ marginTop: 6 }}
              columns={columns as any}
              dataSource={trackKm.map((x) => ({ key: x.cell.key, ...x }))}
              pagination={{ pageSize: 8, hideOnSinglePage: true, size: 'small' }}
              onRow={() => ({ onClick: onOpen, style: { cursor: 'pointer' } })}
            />
            <div style={{ marginTop: 6 }}>
              <Text type="secondary" style={{ fontSize: 11 }}>{t('loc.sub.personLegend')}</Text>
            </div>
            <Button size="small" style={{ marginTop: 8 }} onClick={onOpen}>
              {t('loc.sub.openDrawer')}
            </Button>
          </>
        ) : (
          <>
            <Text type="secondary" style={{ fontSize: 12 }}>{t('loc.sub.noCells')}</Text>
            {hasResult && (
              <div style={{ marginTop: 8 }}>
                <Button size="small" onClick={onOpen}>{t('loc.sub.openDrawer')}</Button>
              </div>
            )}
          </>
        )
      )}
    </CardBase>
  )
}
