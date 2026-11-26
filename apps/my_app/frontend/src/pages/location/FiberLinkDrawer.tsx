/* ============================================================================
 * location/FiberLinkDrawer —— 位置页点光纤链路后的详情
 * ============================================================================
 *
 * 数据是 OLIB 拓扑 PDF 第 1 页数字化的「站点对」边（public/olib/olib-fiber-links.json，
 * 见 backend/scripts/gen_olib_fiber_links.py）。它表达的是「PDF 上画了哪些站到站
 * 连接」，**不是光缆走向、容量或时延，也没有实时 KPI** —— 所以这里只展示：
 *   两端站名、图例类型 / 分组、是否规划中、两端坐标、距位置点的距离，
 * 并明确写出「示意图、无实时指标」，不伪造利用率。
 *
 * 与 MwLinkDrawer 分开的原因：MW 有实时明细（容量/吞吐/趋势），光纤没有；
 * 硬塞进同一个抽屉会让「无数据」看着像「功能没做完」。
 * ========================================================================== */
import { Descriptions, Drawer, Tag, Typography } from 'antd'

import { useI18n } from '../../i18n'
import { fmtKm, fmtLatLon, distanceToSegmentKm } from './geo'
import type { FiberLink } from './useLocationData'

const { Text } = Typography

/** 分组 → 文案键（显式 switch：动态拼 key 会被 check:i18n 拦下）。 */
function groupLabelKey(group: string): string {
  switch (group) {
    case 'f40': return 'loc.fo.group.f40'
    case 'f10': return 'loc.fo.group.f10'
    case 'ipran': return 'loc.fo.group.ipran'
    case 'p26': return 'loc.fo.group.p26'
    case 'plan': return 'loc.fo.group.plan'
    default: return 'loc.fo.group.base'
  }
}

export default function FiberLinkDrawer({
  link, center, onClose,
}: {
  link: FiberLink | null
  center?: { lat: number; lon: number } | null
  onClose: () => void
}) {
  const { t } = useI18n()
  const km = link && center
    ? distanceToSegmentKm(center, { lat: link.aLat, lon: link.aLon }, { lat: link.bLat, lon: link.bLon })
    : null

  return (
    <Drawer
      open={!!link}
      onClose={onClose}
      placement="right"
      width="min(95vw, 34rem)"
      title={link ? `${t('loc.fo.title')} · ${link.aName} ↔ ${link.bName}` : t('loc.fo.title')}
      destroyOnHidden
    >
      {link && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--wh-space-md)' }}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Tag color="purple">{t(groupLabelKey(link.group))}</Tag>
            {link.type && <Tag>{link.type}</Tag>}
            {link.planned && <Tag color="gold">{t('loc.fo.planned')}</Tag>}
            {km != null && <Tag color="blue">{t('loc.fo.distance', { d: fmtKm(km) })}</Tag>}
          </div>

          <Descriptions
            size="small" bordered column={1}
            items={[
              { key: 'a', label: t('loc.fo.endpointA'), children: link.aName || '—' },
              { key: 'b', label: t('loc.fo.endpointB'), children: link.bName || '—' },
              { key: 'ac', label: t('loc.fo.coordsA'), children: fmtLatLon({ lat: link.aLat, lon: link.aLon }) },
              { key: 'bc', label: t('loc.fo.coordsB'), children: fmtLatLon({ lat: link.bLat, lon: link.bLon }) },
              { key: 'g', label: t('loc.fo.type'), children: link.type && link.type !== t(groupLabelKey(link.group)) ? `${t(groupLabelKey(link.group))} · ${link.type}` : t(groupLabelKey(link.group)) },
            ]}
          />

          <Text type="secondary" style={{ fontSize: 12 }}>{t('loc.fo.note')}</Text>
        </div>
      )}
    </Drawer>
  )
}
