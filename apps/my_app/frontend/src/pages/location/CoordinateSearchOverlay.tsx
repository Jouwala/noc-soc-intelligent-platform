/* ============================================================================
 * location/CoordinateSearchOverlay —— 地图上的「按经纬度定位」搜索窗
 * ============================================================================
 * 2026-11-23 用户要求：经纬度输入组件整体移到页面最下面，但**地图上也要保留
 * 一个输入经纬度查询的搜索窗**，常用操作不必滚到底部。
 *
 * 两个入口共用页面同一份 latText / lonText 状态：
 *   · 在地图上输入 → 点「定位」→ 与底部「应用坐标」走同一个 applyLocation()
 *   · 在底部输入 → 地图上这个窗里的值同步变化（同一个 state）
 * 因此两边显示永远一致，不会出现「地图按 A 点、底部写着 B 点」。
 *
 * 位置在地图右下角、attribution 之上；手机号搜索窗在地图右上角，两个错开，
 * 中间仍留出可拖动/缩放的地图区域。
 * ========================================================================== */
import { Button, Input, Typography } from 'antd'
import { EnvironmentOutlined } from '@ant-design/icons'

import { useI18n } from '../../i18n'
import { WH } from '../../theme'

const { Text } = Typography

export default function CoordinateSearchOverlay({
  latText, lonText, onLatText, onLonText, onApply, error,
}: {
  latText: string
  lonText: string
  onLatText: (v: string) => void
  onLonText: (v: string) => void
  onApply: () => void
  error: string
}) {
  const { t } = useI18n()

  return (
    <div
      style={{
        position: 'absolute', right: 10, bottom: 30, zIndex: 1200,
        width: 330, maxWidth: 'calc(100% - 20px)',
        background: WH.cardBg, border: WH.cardBorder,
        borderRadius: 'var(--wh-radius-md, 10px)',
        boxShadow: 'var(--wh-card-shadow)',
        padding: '10px 12px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
        <EnvironmentOutlined style={{ color: WH.teal }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: WH.text }}>
          {t('loc.coord.title')}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <Input
          value={latText}
          onChange={(e) => onLatText(e.target.value)}
          onPressEnter={onApply}
          placeholder={t('loc.coord.latPh')}
          maxLength={20}
          style={{ flex: 1, minWidth: 0 }}
        />
        <Input
          value={lonText}
          onChange={(e) => onLonText(e.target.value)}
          onPressEnter={onApply}
          placeholder={t('loc.coord.lonPh')}
          maxLength={20}
          style={{ flex: 1, minWidth: 0 }}
        />
        <Button type="primary" onClick={onApply}>{t('loc.coord.apply')}</Button>
      </div>
      {error
        ? <Text type="danger" style={{ display: 'block', marginTop: 6, fontSize: 11 }}>{error}</Text>
        : <div style={{ marginTop: 6, fontSize: 11, color: WH.textMuted }}>{t('loc.coord.hint')}</div>}
    </div>
  )
}
