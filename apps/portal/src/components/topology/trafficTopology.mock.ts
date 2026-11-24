// ⚠️ Mock 演示数据 —— 只用于 GET /api/network-topology/traffic 失败或返回空时
// 的前端降级展示，**绝不代表真实网络**。页面检测到本文件的数据时会在顶部
// 显示「Mock 演示数据」徽标，避免把演示值当成 PRTG 实测值。
//
// 结构对齐 Issue #230 Phase 1 的真实接口（apps/my_app/backend/routers/network_topology.py）：
//   generated_at / period / source / data_age_seconds / kpi / nodes[] / links[]
// 流量单位统一 Mbps；容量缺失用 null，绝不用 0 冒充「未知」。

export type TrafficPeriod = 'live' | '1h' | '24h' | '7d';
export type TrafficNodeType = 'device' | 'peer' | 'customer' | 'unknown';
export type TrafficLinkStatus = 'up' | 'unusual' | 'down';
export type TrafficMappingConfidence = 'verified' | 'candidate' | 'discovered';

export interface TrafficDataQuality {
  capacity_known: boolean;
  traffic_available: boolean;
  sample_age_seconds: number | null;
  mapping_confidence: TrafficMappingConfidence;
}

export interface TrafficNode {
  id: number;
  key: string;
  name: string;
  type: TrafficNodeType;
  x: number;
  y: number;
  status: TrafficLinkStatus | 'unknown' | null;
}

export interface TrafficLink {
  id: number;
  key: string;
  sensor_id: number | null;
  source_node: number;
  target_node: number;
  source: string | null;
  target: string | null;
  status: string;
  in_mbps: number | null;
  out_mbps: number | null;
  capacity_ab_mbps: number | null;
  capacity_ba_mbps: number | null;
  utilization_pct: number | null;
  mapping_confidence: TrafficMappingConfidence;
  link_type: string;
  data_quality: TrafficDataQuality;
}

export interface TrafficKpi {
  nodes: number;
  links: number;
  up: number;
  unusual: number;
  down: number;
  over_capacity: number;
  capacity_known: number;
  capacity_total: number;
}

export interface TrafficTopologyData {
  generated_at: string;
  period: TrafficPeriod;
  source: string;
  data_age_seconds: number | null;
  kpi: TrafficKpi;
  nodes: TrafficNode[];
  links: TrafficLink[];
}

// 3 台 device（工具栏筛选项）+ peer / customer / unknown 各若干，共 9 个节点。
// 坐标是 0-1000 的相对坐标，与真实接口一致。
const NODES: TrafficNode[] = [
  { id: 1, key: 'mon-core-01', name: 'MON-CORE-01', type: 'device', x: 170, y: 300, status: 'up' },
  { id: 2, key: 'gbr-core-01', name: 'GBR-CORE-01', type: 'device', x: 500, y: 170, status: 'up' },
  { id: 3, key: 'buc-edge-01', name: 'BUC-EDGE-01', type: 'device', x: 800, y: 300, status: 'up' },
  { id: 4, key: 'isp-orange-as', name: 'ISP-ORANGE-AS', type: 'peer', x: 350, y: 470, status: 'unusual' },
  { id: 5, key: 'isp-lonestar', name: 'ISP-LONESTAR', type: 'peer', x: 90, y: 540, status: 'up' },
  { id: 6, key: 'cust-bank-hq', name: 'CUST-BANK-HQ', type: 'customer', x: 720, y: 90, status: 'up' },
  { id: 7, key: 'cust-mining-site', name: 'CUST-MINING-SITE', type: 'customer', x: 940, y: 470, status: 'unusual' },
  { id: 8, key: 'unk-sensor-77', name: 'UNK-SENSOR-77', type: 'unknown', x: 620, y: 560, status: 'down' },
  { id: 9, key: 'unk-relay-12', name: 'UNK-RELAY-12', type: 'unknown', x: 370, y: 70, status: 'unusual' },
];

// 15 条链路，覆盖：
//  - 8 档利用率（0-1 / 1-10 / 10-25 / 25-40 / 40-55 / 55-70 / 70-85 / 85-100）；
//  - 1 条 >100% OVER CAPACITY；
//  - 5 条容量缺失（含 1 条完全无流量，演示 PRTG 不可达降级）；
//  - Up / Unusual / Down 三种状态与 verified / candidate / discovered 三种映射置信度。
const LINKS: TrafficLink[] = [
  {
    id: 101, key: 'MON-CORE-01__GBR-CORE-01', sensor_id: 5001, source_node: 1, target_node: 2,
    source: 'MON-CORE-01', target: 'GBR-CORE-01', status: 'Up',
    in_mbps: 5, out_mbps: 8, capacity_ab_mbps: 1000, capacity_ba_mbps: 1000, utilization_pct: 0.8,
    mapping_confidence: 'verified', link_type: 'backbone',
    data_quality: { capacity_known: true, traffic_available: true, sample_age_seconds: 18, mapping_confidence: 'verified' },
  },
  {
    id: 102, key: 'MON-CORE-01__BUC-EDGE-01', sensor_id: 5002, source_node: 1, target_node: 3,
    source: 'MON-CORE-01', target: 'BUC-EDGE-01', status: 'Up',
    in_mbps: 60, out_mbps: 90, capacity_ab_mbps: 1000, capacity_ba_mbps: 1000, utilization_pct: 9,
    mapping_confidence: 'verified', link_type: 'backbone',
    data_quality: { capacity_known: true, traffic_available: true, sample_age_seconds: 22, mapping_confidence: 'verified' },
  },
  {
    id: 103, key: 'GBR-CORE-01__BUC-EDGE-01', sensor_id: 5003, source_node: 2, target_node: 3,
    source: 'GBR-CORE-01', target: 'BUC-EDGE-01', status: 'Up',
    in_mbps: 80, out_mbps: 120, capacity_ab_mbps: 400, capacity_ba_mbps: 400, utilization_pct: 30,
    mapping_confidence: 'verified', link_type: 'backbone',
    data_quality: { capacity_known: true, traffic_available: true, sample_age_seconds: 31, mapping_confidence: 'verified' },
  },
  {
    id: 104, key: 'MON-CORE-01__ISP-ORANGE-AS', sensor_id: 5004, source_node: 1, target_node: 4,
    source: 'MON-CORE-01', target: 'ISP-ORANGE-AS', status: 'Unusual',
    in_mbps: 200, out_mbps: 150, capacity_ab_mbps: 1000, capacity_ba_mbps: 1000, utilization_pct: 20,
    mapping_confidence: 'candidate', link_type: 'transit',
    data_quality: { capacity_known: true, traffic_available: true, sample_age_seconds: 47, mapping_confidence: 'candidate' },
  },
  {
    id: 105, key: 'MON-CORE-01__ISP-LONESTAR', sensor_id: 5005, source_node: 1, target_node: 5,
    source: 'MON-CORE-01', target: 'ISP-LONESTAR', status: 'Up',
    in_mbps: 90, out_mbps: 40, capacity_ab_mbps: 200, capacity_ba_mbps: 200, utilization_pct: 45,
    mapping_confidence: 'verified', link_type: 'transit',
    data_quality: { capacity_known: true, traffic_available: true, sample_age_seconds: 26, mapping_confidence: 'verified' },
  },
  {
    id: 106, key: 'GBR-CORE-01__CUST-BANK-HQ', sensor_id: 5006, source_node: 2, target_node: 6,
    source: 'GBR-CORE-01', target: 'CUST-BANK-HQ', status: 'Up',
    in_mbps: 320, out_mbps: 280, capacity_ab_mbps: 500, capacity_ba_mbps: 500, utilization_pct: 64,
    mapping_confidence: 'verified', link_type: 'customer',
    data_quality: { capacity_known: true, traffic_available: true, sample_age_seconds: 15, mapping_confidence: 'verified' },
  },
  {
    id: 107, key: 'BUC-EDGE-01__CUST-MINING-SITE', sensor_id: 5007, source_node: 3, target_node: 7,
    source: 'BUC-EDGE-01', target: 'CUST-MINING-SITE', status: 'Unusual',
    in_mbps: 150, out_mbps: 160, capacity_ab_mbps: 200, capacity_ba_mbps: 200, utilization_pct: 80,
    mapping_confidence: 'candidate', link_type: 'customer',
    data_quality: { capacity_known: true, traffic_available: true, sample_age_seconds: 64, mapping_confidence: 'candidate' },
  },
  {
    id: 108, key: 'GBR-CORE-01__UNK-RELAY-12', sensor_id: 5008, source_node: 2, target_node: 9,
    source: 'GBR-CORE-01', target: 'UNK-RELAY-12', status: 'Up',
    in_mbps: 92, out_mbps: 88, capacity_ab_mbps: 100, capacity_ba_mbps: 100, utilization_pct: 92,
    mapping_confidence: 'discovered', link_type: 'unknown',
    data_quality: { capacity_known: true, traffic_available: true, sample_age_seconds: 40, mapping_confidence: 'discovered' },
  },
  {
    id: 109, key: 'MON-CORE-01__UNK-SENSOR-77', sensor_id: 5009, source_node: 1, target_node: 8,
    source: 'MON-CORE-01', target: 'UNK-SENSOR-77', status: 'Unusual',
    in_mbps: 140, out_mbps: 20, capacity_ab_mbps: 100, capacity_ba_mbps: 100, utilization_pct: 140,
    mapping_confidence: 'discovered', link_type: 'unknown',
    data_quality: { capacity_known: true, traffic_available: true, sample_age_seconds: 35, mapping_confidence: 'discovered' },
  },
  {
    id: 110, key: 'BUC-EDGE-01__ISP-ORANGE-AS', sensor_id: 5010, source_node: 3, target_node: 4,
    source: 'BUC-EDGE-01', target: 'ISP-ORANGE-AS', status: 'Up',
    in_mbps: 45, out_mbps: 30, capacity_ab_mbps: null, capacity_ba_mbps: null, utilization_pct: null,
    mapping_confidence: 'candidate', link_type: 'transit',
    data_quality: { capacity_known: false, traffic_available: true, sample_age_seconds: 55, mapping_confidence: 'candidate' },
  },
  {
    id: 111, key: 'GBR-CORE-01__ISP-LONESTAR', sensor_id: 5011, source_node: 2, target_node: 5,
    source: 'GBR-CORE-01', target: 'ISP-LONESTAR', status: 'Up',
    in_mbps: 8, out_mbps: 4, capacity_ab_mbps: null, capacity_ba_mbps: null, utilization_pct: null,
    mapping_confidence: 'discovered', link_type: 'transit',
    data_quality: { capacity_known: false, traffic_available: true, sample_age_seconds: 28, mapping_confidence: 'discovered' },
  },
  {
    id: 112, key: 'BUC-EDGE-01__CUST-BANK-HQ', sensor_id: 5012, source_node: 3, target_node: 6,
    source: 'BUC-EDGE-01', target: 'CUST-BANK-HQ', status: 'Up',
    in_mbps: 620, out_mbps: 180, capacity_ab_mbps: null, capacity_ba_mbps: null, utilization_pct: null,
    mapping_confidence: 'candidate', link_type: 'customer',
    data_quality: { capacity_known: false, traffic_available: true, sample_age_seconds: 33, mapping_confidence: 'candidate' },
  },
  {
    id: 113, key: 'UNK-SENSOR-77__UNK-RELAY-12', sensor_id: 5013, source_node: 8, target_node: 9,
    source: 'UNK-SENSOR-77', target: 'UNK-RELAY-12', status: 'Unusual',
    in_mbps: 3, out_mbps: 2, capacity_ab_mbps: null, capacity_ba_mbps: null, utilization_pct: null,
    mapping_confidence: 'discovered', link_type: 'unknown',
    data_quality: { capacity_known: false, traffic_available: true, sample_age_seconds: 7200, mapping_confidence: 'discovered' },
  },
  {
    id: 114, key: 'MON-CORE-01__CUST-MINING-SITE', sensor_id: 5014, source_node: 1, target_node: 7,
    source: 'MON-CORE-01', target: 'CUST-MINING-SITE', status: 'Down',
    in_mbps: 210, out_mbps: 195, capacity_ab_mbps: 300, capacity_ba_mbps: 300, utilization_pct: 70,
    mapping_confidence: 'candidate', link_type: 'customer',
    data_quality: { capacity_known: true, traffic_available: true, sample_age_seconds: 120, mapping_confidence: 'candidate' },
  },
  {
    id: 115, key: 'GBR-CORE-01__UNK-SENSOR-77', sensor_id: 5015, source_node: 2, target_node: 8,
    source: 'GBR-CORE-01', target: 'UNK-SENSOR-77', status: 'Down',
    in_mbps: null, out_mbps: null, capacity_ab_mbps: null, capacity_ba_mbps: null, utilization_pct: null,
    mapping_confidence: 'discovered', link_type: 'unknown',
    data_quality: { capacity_known: false, traffic_available: false, sample_age_seconds: 1800, mapping_confidence: 'discovered' },
  },
];

// 固定生成时间与数据年龄：Mock 不做时间旅行，页面也不会把它渲染成「刚刚同步」。
const GENERATED_AT = '2026-01-01T00:00:00Z';

export const TRAFFIC_TOPOLOGY_MOCK: TrafficTopologyData = {
  generated_at: GENERATED_AT,
  period: '24h',
  source: 'MOCK',
  data_age_seconds: 7200,
  kpi: {
    nodes: NODES.length,
    links: LINKS.length,
    up: 9,
    unusual: 4,
    down: 2,
    over_capacity: 1,
    capacity_known: 10,
    capacity_total: LINKS.length,
  },
  nodes: NODES,
  links: LINKS,
};

/** 按所选时间范围返回一份 Mock（深拷贝，调用方可安全展开/筛选）。 */
export function trafficTopologyMock(period: TrafficPeriod): TrafficTopologyData {
  return {
    ...TRAFFIC_TOPOLOGY_MOCK,
    period,
    nodes: TRAFFIC_TOPOLOGY_MOCK.nodes.map((n) => ({ ...n })),
    links: TRAFFIC_TOPOLOGY_MOCK.links.map((l) => ({
      ...l,
      data_quality: { ...l.data_quality },
    })),
  };
}
