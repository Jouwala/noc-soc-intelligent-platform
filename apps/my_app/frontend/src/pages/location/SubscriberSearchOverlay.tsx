/* ============================================================================
 * location/SubscriberSearchOverlay —— 地图上的「查询订阅者」搜索窗口
 * ============================================================================
 * 2026-11-23 用户要求：号码搜索窗放到**地图组件上**（浮在地图右上角），
 * 订阅者轨迹结果表则放到页面最下面。这样地图是「先找用户」的入口，
 * 下面的表只负责展示这次查询的结果，不再承担输入。
 *
 * 实现注意：
 *   · 浮层是 MapContainer 的**兄弟节点**（在卡片的 position:relative body 里），
 *     不塞进 Leaflet 内部 —— 否则输入框的点击/键入会被地图的拖动、缩放事件抢走。
 *   · z-index 1200：Leaflet 控件 1000、弹窗 700，必须高于控件才压得住；
 *     但不要高到盖住抽屉（抽屉是 fixed，层级在 1000+ 且后挂载，仍会正常覆盖）。
 *   · 文案走 i18n；颜色走 WH 令牌，深浅主题自动跟随。
 * ========================================================================== */
import { Button, DatePicker, Input, Tag, Typography } from 'antd'
import { MobileOutlined, SearchOutlined } from '@ant-design/icons'

import { useI18n } from '../../i18n'
import { WH, useIsMobile } from '../../theme'
import { ExplainIcon } from '../../components/MetricExplain'
import {
  clampRange, disabledRangeProps, PAGE_RETENTION_HOURS, type VmaxRange,
} from '../vmaxWindow'

const { Text } = Typography
const { RangePicker } = DatePicker

export default function SubscriberSearchOverlay({
  msisdn, onMsisdnChange, onSearch,
  loading, diagLoading, searched, resultMsisdn,
  cellCount, missing, hasResult, onOpen,
  range, onRangeChange,
}: {
  msisdn: string
  onMsisdnChange: (v: string) => void
  onSearch: () => void
  /** 查询时间窗（默认最近 3 小时，最多回看 24 小时） */
  range: VmaxRange
  onRangeChange: (r: VmaxRange) => void
  loading: boolean
  diagLoading: boolean
  searched: boolean
  resultMsisdn?: string
  cellCount: number
  missing: number
  hasResult: boolean
  onOpen: () => void
}) {
  const { t } = useI18n()
  const isMobile = useIsMobile()
  const busy = loading || diagLoading

  return (
    <div
      style={{
        position: 'absolute', top: 10, right: 10, zIndex: 1200,
        /* 固定 330：门户 GIS Map 栏的 iframe 约 650px 宽，若按移动端铺满会把
           LAYERS 面板与地图上半截全盖住；390px 手机上 330 也仍可用。 */
        width: 330,
        maxWidth: 'calc(100% - 20px)',
        background: WH.cardBg, border: WH.cardBorder,
        borderRadius: 'var(--wh-radius-md, 10px)',
        boxShadow: 'var(--wh-card-shadow)',
        padding: '10px 12px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, minWidth: 0 }}>
        <MobileOutlined style={{ color: WH.teal }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: WH.text, whiteSpace: 'nowrap' }}>
          {t('loc.search.title')}
        </span>
        {resultMsisdn && (
          <Tag color="cyan" style={{ marginInlineStart: 'auto', marginInlineEnd: 0, maxWidth: 130 }}>
            {resultMsisdn}
          </Tag>
        )}
      </div>

      <div style={{ display: 'flex', gap: 6 }}>
        <Input
          prefix={<SearchOutlined style={{ color: WH.textDim }} />}
          value={msisdn}
          onChange={(e) => onMsisdnChange(e.target.value)}
          onPressEnter={onSearch}
          placeholder={t('loc.search.placeholder')}
          maxLength={24}
          allowClear
          style={{ flex: 1, minWidth: 0 }}
        />
        <Button type="primary" loading={busy} onClick={onSearch}>
          {t('loc.search.button')}
        </Button>
      </div>

      {/* 时间窗（2026-11-23 用户要求可设开始时间）：默认最近 3 小时，
          最多回看 24 小时（与 VMAX 页同一保留期）；点「查询」才生效。 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
        <span style={{ fontSize: 11, color: WH.textMuted, whiteSpace: 'nowrap' }}>
          {t('loc.search.time')}
        </span>
        <RangePicker
          size="small"
          value={range}
          allowClear={false}
          showTime={{ format: 'HH:mm' }}
          format="MM-DD HH:mm"
          minuteStep={5}
          style={{ flex: 1, minWidth: 0 }}
          onChange={(v) => {
            if (v && v[0] && v[1]) onRangeChange(clampRange([v[0], v[1]]))
          }}
          {...disabledRangeProps(PAGE_RETENTION_HOURS)}
        />
        <ExplainIcon text={t('loc.search.rangeHint')} size="0.8rem" />
      </div>

      <div style={{ marginTop: 6, fontSize: 11, color: WH.textMuted, lineHeight: 1.5 }}>
        {busy
          ? t('loc.sub.loading')
          : searched && hasResult
            ? (
              <span>
                {t('loc.sub.cells', { n: cellCount })}
                {missing > 0 ? ` · ${t('loc.sub.missingCoords', { n: missing })}` : ''}
                <Button type="link" size="small" style={{ paddingInline: 6 }} onClick={onOpen}>
                  {t('loc.search.openDiag')}
                </Button>
              </span>
            )
            : null}
        {!busy && searched && !hasResult && (
          <span>{t('loc.sub.noCells')}</span>
        )}
        {!busy && !searched && (
          <span style={{ display: 'block' }}>
            {isMobile ? t('loc.search.hint') : t('loc.search.hint2')}
          </span>
        )}
      </div>
    </div>
  )
}
