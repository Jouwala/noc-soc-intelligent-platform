/* ============================================================================
 * 分组表「组内受控排序」纯函数（#319 B1）
 * ----------------------------------------------------------------------------
 * dataSource 是组行 + 链路行交错的扁平数组；不能把 sorter 交给 antd 对整表排序，
 * 否则链路会跨组。排序只发生在每个设备组内部，组顺序与组头统计不变。
 * null / undefined 数值统一排在最后（升序、降序都一样）。
 * ========================================================================== */

import {
  PrtgLink, compareLinksDefault, statusSortRank,
} from './prtgModels'

export type LinksSortKey =
  | 'status'
  | 'inOut'
  | 'errDisc'
  | 'uptime'
  | 'stability'
  | 'lastDown'

export type LinksSortOrder = 'ascend' | 'descend'

export interface LinksSortState {
  key: LinksSortKey | null
  order: LinksSortOrder | null
}

/** null 一律靠后；两边都有值时按数值差，再乘方向 */
export function compareNullableNumber(
  a: number | null | undefined,
  b: number | null | undefined,
  order: LinksSortOrder,
): number {
  const aNull = a == null || !Number.isFinite(a)
  const bNull = b == null || !Number.isFinite(b)
  if (aNull && bNull) return 0
  if (aNull) return 1
  if (bNull) return -1
  const cmp = (a as number) - (b as number)
  return order === 'ascend' ? cmp : -cmp
}

function errorsSum(link: PrtgLink): number | null {
  if (link.errors_in == null && link.errors_out == null) return null
  return (link.errors_in ?? 0) + (link.errors_out ?? 0)
}

function compareByKey(a: PrtgLink, b: PrtgLink, key: LinksSortKey, order: LinksSortOrder): number {
  switch (key) {
    case 'status': {
      const cmp = statusSortRank(a.status) - statusSortRank(b.status)
      return order === 'ascend' ? cmp : -cmp
    }
    case 'inOut':
      return compareNullableNumber(a.in_mbps, b.in_mbps, order)
    case 'errDisc':
      return compareNullableNumber(errorsSum(a), errorsSum(b), order)
    case 'uptime':
      return compareNullableNumber(a.uptime_pct, b.uptime_pct, order)
    case 'stability':
      return compareNullableNumber(a.down_count_24h, b.down_count_24h, order)
    case 'lastDown': {
      const sa = (a.last_down || '').trim()
      const sb = (b.last_down || '').trim()
      const aNull = !sa
      const bNull = !sb
      if (aNull && bNull) return 0
      if (aNull) return 1
      if (bNull) return -1
      const cmp = sa.localeCompare(sb)
      return order === 'ascend' ? cmp : -cmp
    }
    default:
      return 0
  }
}

/** 组内排序；无排序态时沿用默认严重度序。稳定：相等时用 sensor_id。 */
export function sortLinksWithinGroup(
  links: PrtgLink[],
  sort: LinksSortState,
): PrtgLink[] {
  const out = [...links]
  if (!sort.key || !sort.order) {
    out.sort(compareLinksDefault)
    return out
  }
  const key = sort.key
  const order = sort.order
  out.sort((a, b) => {
    const cmp = compareByKey(a, b, key, order)
    if (cmp !== 0) return cmp
    return a.sensor_id - b.sensor_id
  })
  return out
}

export interface DeviceGroup {
  key: string
  raw: string
  links: PrtgLink[]
}

export type FlatGroupRow =
  | {
    rowType: 'group'
    key: string
    deviceRaw: string
    deviceShort: string
    up: number
    unusual: number
    down: number
    unknown: number
    linkCount: number
    deviceKey: string
  }
  | {
    rowType: 'link'
    key: string
    link: PrtgLink
    deviceKey: string
  }

function countStatuses(links: PrtgLink[]): {
  up: number; unusual: number; down: number; unknown: number
} {
  let up = 0, unusual = 0, down = 0, unknown = 0
  for (const l of links) {
    const st = (l.status || '').trim().toLowerCase()
    if (st === 'up') up += 1
    else if (st === 'unusual') unusual += 1
    else if (st === 'down') down += 1
    else unknown += 1
  }
  return { up, unusual, down, unknown }
}

/**
 * 组顺序不变；组内按 sort 排；组头计数来自该组全部链路（与折叠无关）。
 * collapsed[deviceKey]=true 时只出组头、不出链路行。
 */
export function buildGroupedDataSource(
  groups: DeviceGroup[],
  collapsed: Record<string, boolean>,
  sort: LinksSortState,
  shortName: (raw: string) => string,
): FlatGroupRow[] {
  const rows: FlatGroupRow[] = []
  for (const g of groups) {
    const sorted = sortLinksWithinGroup(g.links, sort)
    const counts = countStatuses(g.links)
    rows.push({
      rowType: 'group',
      key: `g:${g.key}`,
      deviceKey: g.key,
      deviceRaw: g.raw,
      deviceShort: shortName(g.raw),
      ...counts,
      linkCount: g.links.length,
    })
    if (collapsed[g.key]) continue
    for (const link of sorted) {
      rows.push({
        rowType: 'link',
        key: `l:${link.sensor_id}`,
        link,
        deviceKey: g.key,
      })
    }
  }
  return rows
}

/** 单测辅助：扁平行序列里，每条链路所属的最近组头 deviceKey */
export function linkRowsUnderGroups(rows: FlatGroupRow[]): Array<{
  deviceKey: string
  sensorIds: number[]
}> {
  const out: Array<{ deviceKey: string; sensorIds: number[] }> = []
  let cur: { deviceKey: string; sensorIds: number[] } | null = null
  for (const row of rows) {
    if (row.rowType === 'group') {
      cur = { deviceKey: row.deviceKey, sensorIds: [] }
      out.push(cur)
    } else if (cur) {
      cur.sensorIds.push(row.link.sensor_id)
    }
  }
  return out
}
