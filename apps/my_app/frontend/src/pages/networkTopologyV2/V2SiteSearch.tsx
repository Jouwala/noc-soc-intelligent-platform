/* ============================================================================
 * OLIB GIS 拓扑 —— 右侧站点搜索
 * ============================================================================
 * 搜索/区域/类型三段过滤，结果列表带「有无 GPS」标记。未定位站点**保留在列表里**
 * （用户需要知道它存在、为什么没上图），但不参与地图绘制。
 * ========================================================================== */
import { Input, Select, Segmented, Button } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import type { MouseEvent } from 'react'
import { useI18n } from '../../i18n'
import { SECTORS, type SiteFilter } from '../networkTopology/topologyData'
import { CardBase } from '../../components/cards'
import { siteKind } from './geoData'
import type { GeoSite } from './types'

const MAX_RESULTS = 60

export default function V2SiteSearch({
  query, onQuery, sector, onSector, filter, onFilter,
  results, total, located, selected, onPick, onShowUnmatched,
}: {
  query: string
  onQuery: (v: string) => void
  sector: string
  onSector: (v: string) => void
  filter: SiteFilter
  onFilter: (v: SiteFilter) => void
  results: GeoSite[]
  total: number
  located: number
  selected: GeoSite | null
  onPick: (s: GeoSite) => void
  onShowUnmatched: (e: MouseEvent<HTMLElement>) => void
}) {
  const { t } = useI18n()
  const shown = results.slice(0, MAX_RESULTS)
  const unmatched = total - located

  const kindTag = (s: GeoSite) => {
    const kind = siteKind(s)
    if (kind === 'core') return <span className="olibv2-tag is-core">{t('olibV2.tag.core')}</span>
    if (kind === 'isp') return <span className="olibv2-tag is-isp">{t('olibV2.tag.isp')}</span>
    if (kind === '5g') return <span className="olibv2-tag is-5g">{t('olibV2.tag.5g')}</span>
    return null
  }

  return (
    <CardBase
      title={t('olibV2.search.title')}
      extra={(
        <Button size="small" type="link" onClick={onShowUnmatched} disabled={!unmatched}>
          {t('olibV2.match.open', { n: unmatched })}
        </Button>
      )}
    >
      <Input
        allowClear
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        onPressEnter={() => { if (results[0]) onPick(results[0]) }}
        placeholder={t('olibV2.search.placeholder')}
        prefix={<SearchOutlined style={{ color: 'var(--wh-text-muted)' }} />}
      />
      <div className="olibv2-filter-row">
        <Select
          size="small"
          value={sector}
          onChange={onSector}
          style={{ flex: '1 1 120px', minWidth: 0 }}
          options={[
            { value: '', label: t('olibV2.search.allSectors') },
            ...SECTORS.map((s) => ({ value: s, label: s })),
          ]}
        />
        <Segmented
          className="wh-seg"
          size="small"
          value={filter}
          onChange={(v) => onFilter(v as SiteFilter)}
          options={[
            { value: 'all', label: t('olibV2.filter.all') },
            { value: '5g', label: '5G' },
            { value: 'isp', label: 'ISP' },
            { value: 'core', label: t('olibV2.tag.core') },
          ]}
        />
      </div>

      <div className="olibv2-matchsum">
        <span>{t('olibV2.match.summary', { located, total })}</span>
        {unmatched > 0 && <span className="is-warn">{t('olibV2.match.unlocated', { n: unmatched })}</span>}
      </div>

      <div className="olibv2-res-count">
        {t('olibV2.search.count', { n: results.length, total })}
      </div>

      {shown.length === 0 ? (
        <div className="olibv2-empty">{t('olibV2.search.empty')}</div>
      ) : (
        <div className="olibv2-res-list">
          {shown.map((s) => (
            <button
              key={`${s.index}-${s.name}`}
              type="button"
              className={`olibv2-res${selected?.index === s.index ? ' is-selected' : ''}${s.located ? '' : ' is-unlocated'}`}
              onClick={() => onPick(s)}
            >
              <span className="olibv2-res-main">
                <span className="olibv2-res-name">{s.name}</span>
                {kindTag(s)}
              </span>
              <span className="olibv2-res-meta">
                {s.located
                  ? [s.county, s.region].filter(Boolean).join(' · ')
                  : t('olibV2.search.noGps')}
              </span>
            </button>
          ))}
          {results.length > shown.length && (
            <div className="olibv2-empty">{t('olibV2.search.truncated', { n: shown.length })}</div>
          )}
        </div>
      )}
    </CardBase>
  )
}
