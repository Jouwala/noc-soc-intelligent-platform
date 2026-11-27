/* ============================================================================
 * 链路健康主表：7 列 + 按设备分组（可折叠）+ 可展开次要字段。
 * 桌面宽度下无横向滚动；行点击打开详情（展开按钮 stopPropagation）。
 * ========================================================================== */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Empty, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { DownOutlined, RightOutlined } from '@ant-design/icons'

import { getNetworkTopologyTraffic } from '../../api'
import { useI18n } from '../../i18n'
import { WH } from '../../theme'
import { CardBase } from '../../components/cards'
import { ExplainIcon, ExplainPopover } from '../../components/MetricExplain'
import { fmtDuration } from './prtgLogModels'
import {
  NA, PrtgLink, cleanSensorName, compareLinksDefault, explainCapacity,
  explainDiscards, explainErrors, explainIn, explainLastDown, explainOut,
  explainSite, explainTotal, explainUnicast, explainUptime, explainUtilization,
  fmtInt, fmtNum, fmtTime, matchLabelKey, shortDeviceName, statusColor,
  statusLabelKey, statusSortRank,
} from './prtgModels'
import { explainStatusReason } from './prtgLogModels'
import {
  TopoLink, TopoNode, buildPeerLabelBySensorId,
} from './prtgTopologyModels'

const { Text } = Typography

type GroupRow = {
  rowType: 'group'
  key: string
  deviceRaw: string
  deviceShort: string
  up: number
  unusual: number
  down: number
  unknown: number
  linkCount: number
}

type LinkRow = {
  rowType: 'link'
  key: string
  link: PrtgLink
  deviceKey: string
}

type TableRow = GroupRow | LinkRow

function isGroup(r: TableRow): r is GroupRow {
  return r.rowType === 'group'
}

function deviceKeyOf(link: PrtgLink): string {
  const raw = (link.device_name || '').trim()
  return raw || `__sensor_${link.sensor_id}`
}

export interface PrtgLinksTableProps {
  links: PrtgLink[]
  loading: boolean
  error: string
  onOpenDetail: (link: PrtgLink, at?: { x: number; y: number }) => void
}

export default function PrtgLinksTable({ links, loading, error, onOpenDetail }: PrtgLinksTableProps) {
  const { t } = useI18n()
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  /** 拓扑对端名（拿不到则空 Map，小字退回只显示设备短名） */
  const [peerBySensor, setPeerBySensor] = useState<Map<number, string>>(() => new Map())

  useEffect(() => {
    let alive = true
    getNetworkTopologyTraffic({ period: 'live' })
      .then(res => {
        if (!alive) return
        const nodes = (res.data?.nodes || []) as TopoNode[]
        const topoLinks = (res.data?.links || []) as TopoLink[]
        setPeerBySensor(buildPeerLabelBySensorId(nodes, topoLinks))
      })
      .catch(() => {
        if (alive) setPeerBySensor(new Map())
      })
    return () => { alive = false }
  }, [])

  const groups = useMemo(() => {
    const map = new Map<string, { raw: string; links: PrtgLink[] }>()
    for (const link of links) {
      const key = deviceKeyOf(link)
      const raw = (link.device_name || '').trim() || t('prtg.group.unknownDevice')
      let g = map.get(key)
      if (!g) {
        g = { raw, links: [] }
        map.set(key, g)
      }
      g.links.push(link)
    }
    for (const g of map.values()) g.links.sort(compareLinksDefault)
    return [...map.entries()].sort((a, b) => {
      const worstA = Math.min(...a[1].links.map(l => statusSortRank(l.status)))
      const worstB = Math.min(...b[1].links.map(l => statusSortRank(l.status)))
      if (worstA !== worstB) return worstA - worstB
      return shortDeviceName(a[1].raw).localeCompare(shortDeviceName(b[1].raw))
    })
  }, [links, t])

  const dataSource = useMemo<TableRow[]>(() => {
    const rows: TableRow[] = []
    for (const [key, g] of groups) {
      let up = 0, unusual = 0, down = 0, unknown = 0
      for (const l of g.links) {
        const st = (l.status || '').trim().toLowerCase()
        if (st === 'up') up += 1
        else if (st === 'unusual') unusual += 1
        else if (st === 'down') down += 1
        else unknown += 1
      }
      rows.push({
        rowType: 'group',
        key: `g:${key}`,
        deviceRaw: g.raw,
        deviceShort: shortDeviceName(g.raw),
        up, unusual, down, unknown,
        linkCount: g.links.length,
      })
      if (!collapsed[key]) {
        for (const link of g.links) {
          rows.push({
            rowType: 'link',
            key: `l:${link.sensor_id}`,
            link,
            deviceKey: key,
          })
        }
      }
    }
    return rows
  }, [groups, collapsed])

  const toggleGroup = useCallback((deviceKey: string) => {
    setCollapsed(prev => ({ ...prev, [deviceKey]: !prev[deviceKey] }))
  }, [])

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

  const columns = useMemo<ColumnsType<TableRow>>(() => [
    {
      title: <>{t('prtg.col.status')} <ExplainIcon text={t('prtg.colExplain.status')} /></>,
      key: 'status',
      width: 110,
      sorter: (a, b) => {
        if (isGroup(a) || isGroup(b)) return 0
        return statusSortRank(a.link.status) - statusSortRank(b.link.status)
      },
      render: (_: unknown, row: TableRow) => {
        if (isGroup(row)) {
          const open = !collapsed[row.key.slice(2)]
          return (
            <button
              type="button"
              onClick={e => {
                e.stopPropagation()
                toggleGroup(row.key.slice(2))
              }}
              style={{
                border: 'none', background: 'transparent', cursor: 'pointer',
                color: WH.text, display: 'inline-flex', alignItems: 'center', gap: 6, padding: 0,
              }}
            >
              {open ? <DownOutlined /> : <RightOutlined />}
              <Text type="secondary" style={{ fontSize: 12 }}>
                {row.linkCount}
              </Text>
            </button>
          )
        }
        const link = row.link
        return (
          <ExplainPopover text={explainStatusReason(link.status, link.status_raw, link.last_message, link.unusual_reason, t)}>
            <Tag color={statusColor(link.status)}>
              {t(statusLabelKey(link.status))}
              {link.unusual_reason ? (link.unusual_reason.direction === 'high' ? ' ↑' : ' ↓') : ''}
            </Tag>
          </ExplainPopover>
        )
      },
    },
    {
      title: <>{t('prtg.col.linkClean')} <ExplainIcon text={t('prtg.colExplain.linkClean')} /></>,
      key: 'link',
      render: (_: unknown, row: TableRow) => {
        if (isGroup(row)) {
          return (
            <div title={row.deviceRaw}>
              <strong style={{ color: WH.text }}>{row.deviceShort}</strong>
              <span style={{ marginLeft: 8, fontSize: 12, color: WH.textMuted }}>
                {t('prtg.group.counts', {
                  up: row.up, unusual: row.unusual, down: row.down, unknown: row.unknown,
                })}
              </span>
            </div>
          )
        }
        const link = row.link
        const cleaned = cleanSensorName(link.sensor_name)
        const device = shortDeviceName(link.device_name)
        const topoPeer = peerBySensor.get(link.sensor_id)
        const sitePeer = isSiteMatchedPeer(link)
        const subtitle = topoPeer
          ? `${device} → ${topoPeer}`
          : (sitePeer ? `${device} · ${sitePeer}` : device)
        return (
          <div title={link.sensor_name || undefined}>
            <div style={{ color: WH.text }}>{cleaned}</div>
            <div style={{ fontSize: 12, color: WH.textMuted }}>{subtitle}</div>
          </div>
        )
      },
    },
    {
      title: <>{t('prtg.col.inOut')} <ExplainIcon text={t('prtg.colExplain.inOut')} /></>,
      key: 'inOut',
      width: 140,
      align: 'right',
      sorter: (a, b) => {
        if (isGroup(a) || isGroup(b)) return 0
        return (a.link.in_mbps ?? -1) - (b.link.in_mbps ?? -1)
      },
      render: (_: unknown, row: TableRow) => {
        if (isGroup(row)) return null
        const link = row.link
        return (
          <ExplainPopover
            placement="left"
            text={`${explainIn(link, t)}\n${explainOut(link, t)}`}
          >
            <span style={{ fontVariantNumeric: 'tabular-nums' }}>
              {fmtNum(link.in_mbps)} / {fmtNum(link.out_mbps)}
            </span>
          </ExplainPopover>
        )
      },
    },
    {
      title: <>{t('prtg.col.errDisc')} <ExplainIcon text={t('prtg.colExplain.errDisc')} /></>,
      key: 'errDisc',
      width: 130,
      align: 'right',
      sorter: (a, b) => {
        if (isGroup(a) || isGroup(b)) return 0
        const ea = (a.link.errors_in ?? 0) + (a.link.errors_out ?? 0)
        const eb = (b.link.errors_in ?? 0) + (b.link.errors_out ?? 0)
        return ea - eb
      },
      render: (_: unknown, row: TableRow) => {
        if (isGroup(row)) return null
        const link = row.link
        const hot = ((link.errors_in ?? 0) + (link.errors_out ?? 0)
          + (link.discards_in ?? 0) + (link.discards_out ?? 0)) > 0
        return (
          <ExplainPopover
            placement="left"
            text={`${explainErrors(link, t)}\n${explainDiscards(link, t)}`}
          >
            <span style={{
              fontVariantNumeric: 'tabular-nums',
              color: hot ? WH.warn : undefined,
            }}>
              {fmtNum(link.errors_in)}/{fmtNum(link.errors_out)}
              <span style={{ color: WH.textMuted }}> · </span>
              {fmtNum(link.discards_in)}/{fmtNum(link.discards_out)}
            </span>
          </ExplainPopover>
        )
      },
    },
    {
      title: <>{t('prtg.col.uptime')} <ExplainIcon text={t('prtg.colExplain.uptime')} /></>,
      key: 'uptime',
      width: 100,
      align: 'right',
      sorter: (a, b) => {
        if (isGroup(a) || isGroup(b)) return 0
        return (a.link.uptime_pct ?? 101) - (b.link.uptime_pct ?? 101)
      },
      render: (_: unknown, row: TableRow) => {
        if (isGroup(row)) return null
        return numCell(row.link.uptime_pct, explainUptime(row.link, t), 3, '%')
      },
    },
    {
      title: <>{t('prtg.col.stabilityShort')} <ExplainIcon text={t('prtg.colExplain.stability')} /></>,
      key: 'stability',
      width: 130,
      align: 'right',
      sorter: (a, b) => {
        if (isGroup(a) || isGroup(b)) return 0
        return (a.link.down_count_24h ?? -1) - (b.link.down_count_24h ?? -1)
      },
      render: (_: unknown, row: TableRow) => {
        if (isGroup(row)) return null
        const link = row.link
        return (
          <ExplainPopover
            placement="left"
            text={t('prtg.cellExplain.stability', {
              down: fmtInt(link.down_count_24h),
              icmp: fmtInt(link.icmp_timeouts_24h),
              mttr: link.mttr_minutes_7d == null ? NA : fmtDuration(link.mttr_minutes_7d * 60),
            })}
          >
            <span style={{
              fontVariantNumeric: 'tabular-nums',
              color: (link.down_count_24h ?? 0) > 0 ? WH.danger : undefined,
            }}>
              {fmtInt(link.down_count_24h)} / {fmtInt(link.icmp_timeouts_24h)}
            </span>
          </ExplainPopover>
        )
      },
    },
    {
      title: <>{t('prtg.col.lastDown')} <ExplainIcon text={t('prtg.colExplain.lastDown')} /></>,
      key: 'lastDown',
      width: 160,
      sorter: (a, b) => {
        if (isGroup(a) || isGroup(b)) return 0
        return (a.link.last_down || '').localeCompare(b.link.last_down || '')
      },
      render: (_: unknown, row: TableRow) => {
        if (isGroup(row)) return null
        return (
          <ExplainPopover text={explainLastDown(row.link, t)} placement="left">
            <span>{fmtTime(row.link.last_down)}</span>
          </ExplainPopover>
        )
      },
    },
  ], [t, numCell, collapsed, toggleGroup, peerBySensor])

  const expandedRowRender = useCallback((row: TableRow) => {
    if (isGroup(row)) return null
    const link = row.link
    const item = (label: string, node: ReactNode) => (
      <div style={{ display: 'flex', gap: 8, marginBottom: 4, fontSize: 12 }}>
        <span style={{ color: WH.textMuted, minWidth: 88 }}>{label}</span>
        <span style={{ color: WH.text }}>{node}</span>
      </div>
    )
    return (
      <div
        style={{ padding: '4px 8px 8px 28px' }}
        onClick={e => e.stopPropagation()}
      >
        {item(t('prtg.col.total'), numCell(link.total_mbps, explainTotal(link, t)))}
        {item(t('prtg.col.unicast'), pairCell(link.unicast_in, link.unicast_out, explainUnicast(link, t)))}
        {item(t('prtg.col.capacity'), (
          <ExplainPopover text={explainCapacity(link, t)} placement="left">
            <span style={{ color: link.capacity_mbps == null ? WH.warn : undefined }}>
              {link.capacity_mbps == null
                ? t('prtg.capacityMissing')
                : `${fmtNum(link.capacity_mbps)} ${t('prtg.trend.unit')}`}
            </span>
          </ExplainPopover>
        ))}
        {item(t('prtg.col.utilization'), numCell(link.utilization_pct, explainUtilization(link, t), 2, '%'))}
        {item(t('prtg.expand.mttr'), (
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>
            {link.mttr_minutes_7d == null ? NA : fmtDuration(link.mttr_minutes_7d * 60)}
          </span>
        ))}
        {item(t('prtg.col.site'), (
          <ExplainPopover text={explainSite(link, t)}>
            <span>
              {link.site_id && link.site_id !== 'Unknown' ? link.site_id : NA}
              {' · '}{link.region || NA}
              {' · '}{t(matchLabelKey(link.match_status))}
            </span>
          </ExplainPopover>
        ))}
        {item(t('prtg.expand.rawDevice'), (
          <span title={link.device_name || undefined}>{link.device_name || NA}</span>
        ))}
        {item(t('prtg.expand.rawSensor'), (
          <span title={link.sensor_name || undefined}>{link.sensor_name || NA}</span>
        ))}
        {item(t('prtg.expand.lastMessage'), (
          <span>{link.last_message || NA}</span>
        ))}
      </div>
    )
  }, [t, numCell, pairCell])

  return (
    <CardBase
      title={<>{t('prtg.table.title')} <ExplainIcon text={t('prtg.table.hint')} /></>}
      extra={(
        <Text type="secondary" style={{ fontSize: 12 }}>
          {links.length === 0 ? NA : `${links.length}`}
        </Text>
      )}
    >
      <Table<TableRow>
        rowKey="key"
        columns={columns}
        dataSource={dataSource}
        loading={loading}
        pagination={false}
        size="small"
        expandable={{
          expandedRowRender,
          rowExpandable: row => !isGroup(row),
          expandRowByClick: false,
        }}
        onRow={row => {
          if (isGroup(row)) {
            return {
              onClick: () => toggleGroup(row.key.slice(2)),
              style: {
                cursor: 'pointer',
                background: 'color-mix(in srgb, var(--wh-teal) 6%, transparent)',
              },
            }
          }
          return {
            onClick: e => {
              const target = e.target as HTMLElement
              if (target.closest('.ant-table-row-expand-icon-cell, .ant-table-row-expand-icon')) return
              onOpenDetail(row.link, { x: e.clientX, y: e.clientY })
            },
            style: { cursor: 'pointer' },
          }
        }}
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
  )
}

function isSiteMatchedPeer(link: PrtgLink): string | null {
  const site = (link.site_id || '').trim()
  if (!site || site.toLowerCase() === 'unknown') return null
  return site
}
