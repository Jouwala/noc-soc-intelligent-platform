/* ============================================================================
 * NOC 统一网络拓扑中心 —— 统一数据模型
 * ============================================================================
 * 三个来源（IPRAN / MW / OLIB）各自的原生结构在 adapters/ 里翻译成本文件的类型，
 * 页面只认这一套模型。字段的上游出处与缺失值规则见
 * `apps/my_app/docs/NOC_TOPOLOGY_DATA_CONTRACT.md`（改这里必须同步改那份）。
 *
 * 三条不可退让的约定：
 *   1. `key = source + ':' + nativeId` —— 来源是设备身份的一部分，禁止按站名合并；
 *   2. 没有采样 = `null` + `trafficAvailable=false`，**不是 0**；
 *   3. 原生业务分级（MW 的 healthy/minor/major/critical）原样保留在 `nativeStatus`，
 *      8 档流量色阶只表达负载，不重新定义故障严重度。
 * ========================================================================== */

/** 三个数据来源。值同时用作 i18n key 后缀与节点 key 前缀。 */
export type SourceKey = 'ipran' | 'mw' | 'olib'

export const SOURCE_KEYS: readonly SourceKey[] = ['ipran', 'mw', 'olib'] as const

/**
 * 统一状态。刻意保留 MW 的四级名（ok 对应 healthy），
 * 这样「MW 链路 70-80% = major」这件事在中心里仍然可读。
 */
export type NocStatus =
  | 'ok'
  | 'minor'
  | 'major'
  | 'critical'
  | 'down'
  | 'unknown'
  | 'static'
  | 'planned'

export type NocDeviceType =
  | 'core'
  | 'aggregation'
  | 'switch'
  | 'access'
  | 'firewall'
  | 'isp'
  | 'microwave'
  | 'site'

/** 链路介质。`schematic` = 只有连接关系、没有路由/流量依据的示意线。 */
export type NocMedium = 'fiber' | 'microwave' | 'schematic'

export type NocView = 'overview' | 'logical' | 'traffic' | 'geo'

export type SourceState =
  | 'loading'
  | 'connected'
  | 'partial'
  | 'stale'
  | 'unavailable'
  | 'static'

export interface NocAlarmCounts {
  crit: number
  major: number
  minor: number
  warn: number
}

export interface NocNode {
  /** `${source}:${nativeId}` —— 全局唯一，跨来源不合并 */
  key: string
  source: SourceKey
  nativeId: string
  name: string
  ip?: string
  siteId?: string
  /** 上游站名，仅用于展示；不参与跨来源关联 */
  siteName?: string
  latitude?: number | null
  longitude?: number | null
  deviceType: NocDeviceType
  layer?: string
  status: NocStatus
  alarms?: NocAlarmCounts
  /** 在**当前筛选结果**里的度数（由 model.ts 计算，不是原生 deg） */
  degree: number
}

export interface NocCheckpoint {
  /** 源侧端口 / PM checkpoint */
  port: string
  /** 宿侧端口 */
  peerPort?: string
  txThroughput?: number | null
  rxThroughput?: number | null
  txUtilization?: number | null
  rxUtilization?: number | null
}

export interface NocHistoryPoint {
  /** 日粒度是 `YYYY-MM-DD`，实时是完整 Begin Time */
  period: string
  txUtilization: number | null
  rxUtilization: number | null
  maxUtilization: number | null
}

export interface NocEdge {
  key: string
  source: SourceKey
  /** NocNode.key */
  sourceNode: string
  targetNode: string
  medium: NocMedium
  /** 链路两端展示名（抽屉/风险榜用，避免再查一遍节点表） */
  sourceName: string
  targetName: string

  capacityMbps?: number | null
  /**
   * 源设备 → 宿设备的发送利用率（%）。
   * IPRAN: 源侧端口 tx；MW: zte_mw_links 锚定 a→b 的 tx；OLIB: 恒 null。
   */
  txUtilization?: number | null
  /** 宿设备 → 源设备的接收利用率（%），与 tx 分开，禁止平均。 */
  rxUtilization?: number | null

  status: NocStatus
  /** 原生状态名（MW: healthy/minor/major/critical/unknown），用于详情原样展示 */
  nativeStatus?: string
  trafficAvailable: boolean
  planned: boolean
  /** true = 连接关系示意，不代表实际光缆路由 */
  schematic: boolean

  /** IPRAN 折叠前的物理链路名，最多 12 条 */
  ports?: string[]
  checkpoints?: NocCheckpoint[]
  history?: NocHistoryPoint[]
}

export interface SourceMetadata {
  source: SourceKey
  state: SourceState
  /** 上游客服端取数完成时间（ISO） */
  fetchedAt?: string
  /** 上游采样时间（IPRAN: KPI 采样时刻；MW: 选中日期/槽位） */
  sampledAt?: string
  /** 数据粒度：`15min` / `1day` / 静态 */
  granularity?: string
  stale: boolean
  /** 本次是否真的取到了「流量」数据（IPRAN 只有 traffic=1 才算；OLIB 恒 false）。
   *  覆盖率的分母只统计 trafficLoaded 的来源 —— 没取 ≠ 没覆盖。 */
  trafficLoaded?: boolean
  /** 0..1；仅「有流量/有 KPI 覆盖」的来源给出 */
  coverage?: number
  warnings: string[]
  /** 结构化的补充计数（如 OLIB 被跳过的链路数），由界面按 i18n 模板展示 */
  stats?: Record<string, number>
  nodeCount: number
  edgeCount: number
  /** 取数失败原因（state=unavailable 时） */
  error?: string
}

export interface NocModel {
  nodes: NocNode[]
  edges: NocEdge[]
  sources: SourceMetadata[]
}

export const EMPTY_MODEL: NocModel = { nodes: [], edges: [], sources: [] }

/** 单个来源适配后的产物（一个来源 = 一组节点/边 + 自己的元信息） */
export interface SourceSlice {
  nodes: NocNode[]
  edges: NocEdge[]
  meta: SourceMetadata
}

/** 当前筛选条件。搜索只匹配设备名/IP/站名，不做模糊跨源合并。 */
export interface NocFilter {
  source: SourceKey | 'all'
  query: string
  /** 只看有流量数据的链路 */
  trafficOnly: boolean
  /** 只看非 ok 状态的链路/设备 */
  problemsOnly: boolean
}
