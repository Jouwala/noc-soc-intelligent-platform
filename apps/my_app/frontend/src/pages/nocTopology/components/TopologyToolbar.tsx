/* ============================================================================
 * NOC 拓扑中心 —— 工具条
 * ============================================================================
 * 两行：
 *   第一行 视图（总览/逻辑/流量/地理）+ 来源（全部/IPRAN/MW/OLIB）+ 搜索；
 *   第二行 分层（仅 IPRAN）、MW 粒度、标签、只看有流量、只看问题、
 *          缩放/适配（仅 SVG 视图）、刷新、取数时刻。
 *
 * 视图与来源是**两个独立维度**：可以只看 MW 的地理分布，也可以看全部来源的
 * 流量视图。工具条不持有状态，全部由页面传入 —— 便于把 URL 深链接进来。
 * ========================================================================== */
import { Button, Input, Segmented, Select, Space, Switch, Tooltip } from 'antd'
import {
  AimOutlined, ExpandOutlined, ReloadOutlined, ZoomInOutlined, ZoomOutOutlined,
} from '@ant-design/icons'

import { WH } from '../../../theme'
import { useI18n } from '../../../i18n'
import { CardBase } from '../../../components/cards'
import { LAYER_KEYS, LAYER_LABEL_KEY } from '../../ipranTopo/layerDefs'
import type { LayerKey } from '../../ipranTopo/types'
import { SOURCE_LABEL_KEY, VIEW_HINT_KEY, VIEW_LABEL_KEY } from '../registry'
import type { NocView, SourceKey } from '../types'

export type SourceFilter = SourceKey | 'all'

const VIEWS: NocView[] = ['overview', 'logical', 'traffic', 'geo']
const SOURCES: SourceFilter[] = ['all', 'ipran', 'mw', 'olib']

export interface TopologyToolbarProps {
  view: NocView
  onView: (v: NocView) => void
  source: SourceFilter
  onSource: (s: SourceFilter) => void
  layer: LayerKey
  onLayer: (l: LayerKey) => void
  mwRealtime: boolean
  onMwGranularity: (realtime: boolean) => void
  query: string
  onQuery: (q: string) => void
  onLocate: () => void
  showLabels: boolean
  onShowLabels: (v: boolean) => void
  trafficOnly: boolean
  onTrafficOnly: (v: boolean) => void
  problemsOnly: boolean
  onProblemsOnly: (v: boolean) => void
  loading: boolean
  refreshing: boolean
  onRefresh: () => void
  onZoomIn: () => void
  onZoomOut: () => void
  onFit: () => void
  metaLine: string | null
  /** 搜索没命中时的提示（由页面判断后传入） */
  searchMiss: boolean
}

export default function TopologyToolbar(p: TopologyToolbarProps) {
  const { t } = useI18n()
  const svgView = p.view === 'logical' || p.view === 'traffic'
  const layerEnabled = p.source === 'all' || p.source === 'ipran'
  const mwEnabled = p.source === 'all' || p.source === 'mw'

  return (
    <>
      <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
        <Space wrap size={12} align="center">
          <span style={{ color: WH.textMuted, fontSize: 13 }}>{t('nocTopo.toolbar.view')}</span>
          <Segmented
            className="wh-seg"
            size="small"
            value={p.view}
            onChange={(v) => p.onView(v as NocView)}
            options={VIEWS.map((v) => ({ label: t(VIEW_LABEL_KEY[v]), value: v }))}
          />
          <span style={{ color: WH.textMuted, fontSize: 13 }}>{t('nocTopo.toolbar.source')}</span>
          <Segmented
            className="wh-seg"
            size="small"
            value={p.source}
            onChange={(v) => p.onSource(v as SourceFilter)}
            options={SOURCES.map((s) => ({
              label: s === 'all' ? t('nocTopo.source.all') : t(SOURCE_LABEL_KEY[s]),
              value: s,
            }))}
          />
          <Input.Search
            allowClear
            size="small"
            style={{ width: 260 }}
            placeholder={t('nocTopo.toolbar.searchPh')}
            value={p.query}
            onChange={(e) => p.onQuery(e.target.value)}
            onSearch={() => p.onLocate()}
            status={p.searchMiss ? 'warning' : undefined}
          />
          <span style={{ color: WH.textDim, fontSize: 11 }}>{t(VIEW_HINT_KEY[p.view])}</span>
        </Space>
      </CardBase>

      <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
        <Space wrap size={12} align="center">
          <span style={{ color: WH.textMuted, fontSize: 13 }}>{t('nocTopo.toolbar.layer')}</span>
          <Select
            size="small"
            style={{ width: 150 }}
            disabled={!layerEnabled}
            value={p.layer}
            onChange={(v) => p.onLayer(v as LayerKey)}
            options={LAYER_KEYS.map((k) => ({ value: k, label: t(LAYER_LABEL_KEY[k]) }))}
          />

          <span style={{ color: WH.textMuted, fontSize: 13 }}>{t('nocTopo.toolbar.granularity')}</span>
          <Segmented
            className="wh-seg"
            size="small"
            disabled={!mwEnabled}
            value={p.mwRealtime ? 'rt' : 'day'}
            onChange={(v) => p.onMwGranularity(v === 'rt')}
            options={[
              { label: t('nocTopo.toolbar.granularityDay'), value: 'day' },
              { label: t('nocTopo.toolbar.granularityRt'), value: 'rt' },
            ]}
          />

          <Tooltip title={t('nocTopo.toolbar.labelsHint')}>
            <Switch size="small" checked={p.showLabels} onChange={p.onShowLabels}
              checkedChildren={t('nocTopo.toolbar.labels')} unCheckedChildren={t('nocTopo.toolbar.labels')} />
          </Tooltip>
          <Switch size="small" checked={p.trafficOnly} onChange={p.onTrafficOnly}
            checkedChildren={t('nocTopo.toolbar.trafficOnly')} unCheckedChildren={t('nocTopo.toolbar.trafficOnly')} />
          <Switch size="small" checked={p.problemsOnly} onChange={p.onProblemsOnly}
            checkedChildren={t('nocTopo.toolbar.problemsOnly')} unCheckedChildren={t('nocTopo.toolbar.problemsOnly')} />

          {svgView && (
            <>
              <Button size="small" icon={<ZoomInOutlined />} onClick={p.onZoomIn} aria-label={t('nocTopo.toolbar.zoomIn')} />
              <Button size="small" icon={<ZoomOutOutlined />} onClick={p.onZoomOut} aria-label={t('nocTopo.toolbar.zoomOut')} />
              <Button size="small" icon={<ExpandOutlined />} onClick={p.onFit}>{t('nocTopo.toolbar.fit')}</Button>
            </>
          )}
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={p.loading || p.refreshing}
            onClick={p.onRefresh}
          >
            {t('nocTopo.toolbar.refresh')}
          </Button>
          {p.metaLine && (
            <span style={{ color: WH.textDim, fontSize: 11, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <AimOutlined />
              {p.metaLine}
            </span>
          )}
        </Space>
      </CardBase>
    </>
  )
}
