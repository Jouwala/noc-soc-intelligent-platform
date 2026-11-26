/* ============================================================================
 * NOC 统一网络拓扑中心（?page=nocTopology）
 * ============================================================================
 * 定位：**统一展示入口**，不是三个专页的替代品。
 *   · IPRAN / OLIB / MW 原有页面继续负责各自的专业分析；
 *   · 本页负责「一眼看全三个域 + 跨域定位 + 统一视觉语言」。
 *
 * 四个视图：
 *   overview —— 全网总览：地理分布 + 四个汇总数字 + 来源健康；
 *   logical  —— 逻辑拓扑：SVG 分层环，状态色；
 *   traffic  —— 流量视图：Weathermap 8 档色阶，TX/RX 分段；
 *   geo      —— 地理视图：Leaflet + 自托管底图，保真实比例。
 *
 * 明确不做（1.0 边界）：
 *   · 不做跨系统设备/链路的自动关联 —— 设备身份 = source + nativeId；
 *   · 不给 OLIB 编造实时利用率，不给 IPRAN 编造历史曲线；
 *   · 不 iframe 嵌旧页面 —— MW 走 API、OLIB 走静态资产、IPRAN 走既有 API。
 *
 * 自动刷新：已登记进 App.tsx 的 AR_INPLACE_REFRESH_PAGES，监听 `myapp-refresh`
 * 后静默重取，不重建地图/画布（见 hooks/useTopologyRefresh.ts）。
 * ========================================================================== */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Empty, Spin } from 'antd'

import { WH } from '../../theme'
import { useI18n } from '../../i18n'
import { CardBase, StatCard } from '../../components/cards'
import { ExplainIcon } from '../../components/MetricExplain'
import CalcChainNote, { type CalcChainSection } from '../../components/CalcChainNote'
import { formatDateTime } from '../../datetime'
import { isLayerKey } from '../ipranTopo/layerDefs'
import type { LayerKey } from '../ipranTopo/types'
import DeviceDetailDrawer from './components/DeviceDetailDrawer'
import EntityListDrawer from './components/EntityListDrawer'
import GeographicMap from './components/GeographicMap'
import LinkDetailDrawer from './components/LinkDetailDrawer'
import SourceHealthPanel from './components/SourceHealthPanel'
import TopologyCanvas, { type ViewCmd } from './components/TopologyCanvas'
import TopologyLegend from './components/TopologyLegend'
import TopologyToolbar, { type SourceFilter } from './components/TopologyToolbar'
import TopRiskPanel from './components/TopRiskPanel'
import { useTopologyRefresh } from './hooks/useTopologyRefresh'
import { useTopologySelection } from './hooks/useTopologySelection'
import { useTopologySources } from './hooks/useTopologySources'
import { filterModel, incidentEdges, nodeMap, summarize, topRiskEdges } from './model'
import type { NocView } from './types'

const VIEWS: NocView[] = ['overview', 'logical', 'traffic', 'geo']
const SOURCES: SourceFilter[] = ['all', 'ipran', 'mw', 'olib']

function readParam(name: string): string | null {
  try { return new URLSearchParams(window.location.search).get(name) } catch { return null }
}

function readView(): NocView {
  const v = readParam('view')
  return VIEWS.includes(v as NocView) ? (v as NocView) : 'overview'
}

function readSource(): SourceFilter {
  const s = readParam('source')
  return SOURCES.includes(s as SourceFilter) ? (s as SourceFilter) : 'all'
}

function readLayer(): LayerKey {
  const l = readParam('layer')
  return isLayerKey(l) ? l : 'ipran'
}

/** URL 里显式给了粒度才返回 true/false；没给返回 null（交给「按视图默认」） */
function readGranularity(): boolean | null {
  const g = readParam('gran')
  if (g === 'rt') return true
  if (g === 'day') return false
  return null
}

/** 用户没手动选过粒度时：流量视图默认 15 分钟（值班看的是「现在」），其余视图默认日粒度 */
const defaultRealtime = (v: NocView) => v === 'traffic'

/** 画布卡片标题：显式 switch，避免动态拼 i18n key（check:i18n 会拦） */
function canvasTitleKey(view: NocView): string {
  switch (view) {
    case 'logical': return 'nocTopo.canvas.titleLogical'
    case 'traffic': return 'nocTopo.canvas.titleTraffic'
    case 'geo': return 'nocTopo.canvas.titleGeo'
    default: return 'nocTopo.canvas.titleOverview'
  }
}

export default function NocTopologyCenter() {
  const { t } = useI18n()

  const [initialGran] = useState(readGranularity)
  const [view, setView] = useState<NocView>(readView)
  const [source, setSource] = useState<SourceFilter>(readSource)
  const [layer, setLayer] = useState<LayerKey>(readLayer)
  /** 用户是否明确选过粒度（URL 带 gran，或点过粒度切换）。选过就不再随视图自动切 */
  const [granTouched, setGranTouched] = useState(initialGran !== null)
  const [mwRealtime, setMwRealtime] = useState<boolean>(() => initialGran ?? defaultRealtime(readView()))
  const onMwGranularity = useCallback((rt: boolean) => {
    setGranTouched(true)
    setMwRealtime(rt)
  }, [])
  useEffect(() => {
    if (!granTouched) setMwRealtime(defaultRealtime(view))
  }, [view, granTouched])

  const [query, setQuery] = useState('')
  const [showLabels, setShowLabels] = useState(false)
  const [trafficOnly, setTrafficOnly] = useState(false)
  const [problemsOnly, setProblemsOnly] = useState(false)
  const [searchMiss, setSearchMiss] = useState(false)
  const [viewCmd, setViewCmd] = useState<ViewCmd | null>(null)
  const [resetToken, setResetToken] = useState(0)
  /** 汇总卡下钻：设备 / 链路清单 */
  const [listKind, setListKind] = useState<'node' | 'edge' | null>(null)

  const sources = useTopologySources({
    layer,
    includeTraffic: view === 'traffic',
    mwRealtime,
  })
  const refresher = useTopologyRefresh(sources.refresh)
  const selection = useTopologySelection(sources.model)

  /* 视图 / 来源 / 层变化 → 画布回全景、地图回全国视角 */
  useEffect(() => { setResetToken((n) => n + 1) }, [view, source, layer, mwRealtime])

  /* URL 深链接：?page=nocTopology&view=traffic&source=mw&layer=ipran&gran=rt */
  useEffect(() => {
    try {
      const sp = new URLSearchParams(window.location.search)
      sp.set('view', view)
      sp.set('source', source)
      sp.set('layer', layer)
      // 只有用户明确选过才写进 URL；否则留空，打开链接时仍按视图走默认
      if (granTouched) sp.set('gran', mwRealtime ? 'rt' : 'day')
      else sp.delete('gran')
      window.history.replaceState(null, '', `${window.location.pathname}?${sp.toString()}`)
    } catch { /* 隐私模式/禁用 history 时静默降级 */ }
  }, [view, source, layer, mwRealtime, granTouched])

  const filtered = useMemo(
    () => filterModel(sources.model, { source, query, trafficOnly, problemsOnly }),
    [sources.model, source, query, trafficOnly, problemsOnly],
  )
  /** 汇总卡不跟随搜索词：搜索只影响画布，不该让上面的数字瞬间归零 */
  const scope = useMemo(
    () => filterModel(sources.model, { source, query: '', trafficOnly, problemsOnly }),
    [sources.model, source, trafficOnly, problemsOnly],
  )
  const summary = useMemo(() => summarize(scope), [scope])
  const risks = useMemo(() => topRiskEdges(scope, 8), [scope])
  const byKey = useMemo(() => nodeMap(sources.model), [sources.model])

  const selectedNode = selection.node
  const selectedEdge = selection.edge
  const incident = useMemo(
    () => (selectedNode ? incidentEdges(sources.model, selectedNode.key) : []),
    [sources.model, selectedNode],
  )
  const edgeSource = selectedEdge ? byKey.get(selectedEdge.sourceNode) ?? null : null
  const edgeTarget = selectedEdge ? byKey.get(selectedEdge.targetNode) ?? null : null

  const locate = useMemo(
    () => (selection.nodeKey && selection.focusToken
      ? { key: selection.nodeKey, token: selection.focusToken }
      : null),
    [selection.nodeKey, selection.focusToken],
  )

  const bumpView = useCallback((kind: 'in' | 'out' | 'fit') => {
    setViewCmd({ seq: Date.now(), kind })
  }, [])

  const onLocate = useCallback(() => {
    const hit = selection.locate(query)
    setSearchMiss(!!query.trim() && !hit)
  }, [selection, query])

  const metaLine = sources.lastSuccessAt
    ? t('nocTopo.meta.line', {
      at: formatDateTime(new Date(sources.lastSuccessAt)),
      n: filtered.nodes.length,
      m: filtered.edges.length,
    })
    : null

  const coverageText = summary.trafficCoverage == null
    ? '—'
    : `${(summary.trafficCoverage * 100).toFixed(0)}%`

  const calcSections: CalcChainSection[] = [
    { title: t('nocTopo.calc.source'), lines: [t('nocTopo.calc.sourceBody')] },
    { title: t('nocTopo.calc.traffic'), lines: [t('nocTopo.calc.trafficBody')] },
    { title: t('nocTopo.calc.identity'), lines: [t('nocTopo.calc.identityBody')] },
    { title: t('nocTopo.calc.olib'), lines: [t('nocTopo.calc.olibBody')] },
    { title: t('nocTopo.calc.history'), lines: [t('nocTopo.calc.historyBody')] },
  ]

  const loading = sources.loading && sources.model.nodes.length === 0
  const empty = filtered.nodes.length === 0

  /* 只看 MW 时，「严重」= 利用率 > 80%（MW 原生四级，见数据契约 §3.2），是负载高不是故障。
     沿用「严重链路」字样会被读成 26 条链路坏了，所以换成「高负载链路」并用警示色。 */
  const mwOnly = source === 'mw'

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)',
      flex: 1, minHeight: 0,
    }}>
      <div style={{ flexShrink: 0 }}>
        <TopologyToolbar
          view={view} onView={setView}
          source={source} onSource={setSource}
          layer={layer} onLayer={setLayer}
          mwRealtime={mwRealtime} onMwGranularity={onMwGranularity}
          query={query} onQuery={(q) => { setQuery(q); setSearchMiss(false) }} onLocate={onLocate}
          showLabels={showLabels} onShowLabels={setShowLabels}
          trafficOnly={trafficOnly} onTrafficOnly={setTrafficOnly}
          problemsOnly={problemsOnly} onProblemsOnly={setProblemsOnly}
          loading={loading} refreshing={refresher.refreshing}
          onRefresh={() => void refresher.refreshNow()}
          onZoomIn={() => bumpView('in')}
          onZoomOut={() => bumpView('out')}
          onFit={() => bumpView('fit')}
          metaLine={metaLine}
          searchMiss={searchMiss}
        />
      </div>

      {/* 四个汇总数字：跟随来源筛选，不跟随搜索词 */}
      <div style={{
        flexShrink: 0,
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
        gap: 12,
      }}>
        <StatCard
          title={<span>{t('nocTopo.kpi.nodes')} <ExplainIcon text={t('nocTopo.kpi.nodesExplain')} /></span>}
          value={summary.nodes}
          explainText={t('nocTopo.kpi.nodesExplain')}
          accentColor={WH.teal}
          onClick={() => setListKind('node')}
        />
        <StatCard
          title={<span>{t('nocTopo.kpi.links')} <ExplainIcon text={t('nocTopo.kpi.linksExplain')} /></span>}
          value={summary.activeLinks}
          explainText={t('nocTopo.kpi.linksExplain')}
          subText={t('nocTopo.kpi.linksSub', { n: summary.links })}
          accentColor={WH.blue}
          onClick={() => setListKind('edge')}
        />
        <StatCard
          title={(
            <span>
              {t(mwOnly ? 'nocTopo.kpi.criticalMw' : 'nocTopo.kpi.critical')}{' '}
              <ExplainIcon text={t(mwOnly ? 'nocTopo.kpi.criticalMwExplain' : 'nocTopo.kpi.criticalExplain')} />
            </span>
          )}
          value={summary.criticalLinks}
          explainText={t(mwOnly ? 'nocTopo.kpi.criticalMwExplain' : 'nocTopo.kpi.criticalExplain')}
          subText={mwOnly ? t('nocTopo.kpi.criticalMwSub') : t('nocTopo.kpi.criticalSub', { n: summary.downLinks })}
          accentColor={summary.criticalLinks > 0 ? (mwOnly ? WH.warn : WH.danger) : WH.ok}
          onClick={() => setProblemsOnly((v) => !v)}
        />
        <StatCard
          title={<span>{t('nocTopo.kpi.coverage')} <ExplainIcon text={t('nocTopo.kpi.coverageExplain')} /></span>}
          value={coverageText}
          explainText={t('nocTopo.kpi.coverageExplain')}
          subText={`${t('nocTopo.kpi.coverageSub', { n: summary.trafficLinks })}${
            summary.trafficPending.length ? ` · ${t('nocTopo.kpi.coveragePending')}` : ''
          }`}
          accentColor={WH.warn}
          onClick={() => setView('traffic')}
        />
      </div>

      <div style={{
        display: 'flex', gap: 12, alignItems: 'stretch', flexWrap: 'wrap',
        flex: 1, minHeight: 0,
      }}>
        <CardBase
          title={t(canvasTitleKey(view))}
          extra={<ExplainIcon text={t('nocTopo.canvas.hint')} placement="bottomLeft" icon="question" />}
          style={{
            flex: '2 1 620px', minWidth: 0, minHeight: 400,
            display: 'flex', flexDirection: 'column', overflow: 'hidden',
          }}
          bodyStyle={{
            flex: '1 1 0%', minHeight: 0, height: 'auto',
            display: 'flex', flexDirection: 'column', overflow: 'hidden',
            padding: 0,
          }}
        >
          <div style={{ flex: '1 1 0%', minHeight: 0, position: 'relative' }}>
            {loading ? (
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Spin />
              </div>
            ) : empty ? (
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Empty description={query.trim() ? t('nocTopo.canvas.noMatch') : t('nocTopo.canvas.empty')} />
              </div>
            ) : view === 'geo' || view === 'overview' ? (
              <GeographicMap
                nodes={filtered.nodes}
                edges={filtered.edges}
                mode={view === 'overview' ? 'overview' : 'geo'}
                selectedNode={selection.nodeKey}
                onSelectNode={selection.selectNode}
                resetToken={resetToken}
                locate={locate}
              />
            ) : (
              <TopologyCanvas
                nodes={filtered.nodes}
                edges={filtered.edges}
                mode={view === 'traffic' ? 'traffic' : 'logical'}
                selectedNode={selection.nodeKey}
                selectedEdge={selection.edgeKey}
                onSelectNode={selection.selectNode}
                onSelectEdge={selection.selectEdge}
                onBackground={selection.clear}
                showLabels={showLabels}
                focus={selection.nodeKey}
                viewCmd={viewCmd}
                resetKey={`${view}:${source}:${layer}:${mwRealtime}`}
                locate={locate}
              />
            )}
          </div>
          <div style={{ flexShrink: 0, borderTop: `1px solid ${WH.cardBorder}`, padding: '0 var(--wh-space-sm)' }}>
            <TopologyLegend view={view} />
          </div>
        </CardBase>

        <div style={{
          flex: '1 1 300px', minWidth: 280,
          minHeight: 0, display: 'flex', flexDirection: 'column', gap: 12,
        }}>
          <div style={{ flexShrink: 0 }}>
            <SourceHealthPanel sources={sources.model.sources} />
          </div>
          <TopRiskPanel edges={risks} onSelect={selection.selectEdge} />
        </div>
      </div>

      {sources.model.sources.every((s) => s.state === 'unavailable') && (
        <Alert
          type="error"
          showIcon
          message={t('nocTopo.allFailed')}
        />
      )}

      <div style={{ flexShrink: 0 }}>
        <CalcChainNote title={t('nocTopo.calc.title')} sections={calcSections} />
      </div>

      <EntityListDrawer
        open={listKind !== null}
        kind={listKind ?? 'node'}
        nodes={scope.nodes}
        edges={scope.edges}
        onClose={() => setListKind(null)}
        onSelectNode={(key) => { setListKind(null); selection.selectNode(key) }}
        onSelectEdge={(key) => { setListKind(null); selection.selectEdge(key) }}
      />
      <DeviceDetailDrawer
        open={!!selectedNode}
        node={selectedNode}
        incident={incident}
        nodesByKey={byKey}
        onClose={selection.clear}
        onOpenEdge={(key) => { selection.selectEdge(key) }}
      />
      <LinkDetailDrawer
        open={!!selectedEdge}
        edge={selectedEdge}
        sourceNode={edgeSource}
        targetNode={edgeTarget}
        onClose={selection.clear}
        onSelectNode={selection.selectNode}
      />
    </div>
  )
}
