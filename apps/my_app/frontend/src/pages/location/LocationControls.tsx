/* ============================================================================
 * location/LocationControls —— 位置页顶部控制条
 * ============================================================================
 * 从 pages/LocationMap.tsx 拆出（该页原 539 行，AGENTS 三.2 单文件 ≤500 行）。
 *
 * 三组控件，接收/应用坐标的状态全部由页面持有，本组件只负责排版与回调：
 *   ① 坐标（手动输入 / 浏览器定位） + 半径 / 站点数 + 全览 / 刷新
 *   ② 问问答助手取位置（提问 → /api/portal-qa/ask → 解析坐标）
 *   ③ 状态反馈：坐标错误、问答错误/兜底说明、答案原文、扩圈提示
 * ========================================================================== */
import { Alert, Button, Input, Select, Space, Typography } from 'antd'
import { AimOutlined, LinkOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'

import { useI18n } from '../../i18n'
import { CardBase } from '../../components/cards'
import { ExplainIcon } from '../../components/MetricExplain'
import { fmtKm } from './geo'

const { Text } = Typography

const RADIUS_OPTIONS = [5, 10, 15, 30, 50, 100]
const LIMIT_OPTIONS = [6, 12, 20, 30]

export default function LocationControls({
  latText, lonText, onLatText, onLonText, onApply,
  geoBusy, onUseMyLocation,
  radiusKm, onRadiusKm, siteLimit, onSiteLimit, onFit, onRefresh,
  qaQuestion, onQaQuestion, qaBusy, onAskQa,
  qaError, qaNote, qaAnswer, locError,
  widened,
}: {
  latText: string
  lonText: string
  onLatText: (v: string) => void
  onLonText: (v: string) => void
  onApply: () => void
  geoBusy: boolean
  onUseMyLocation: () => void
  radiusKm: number
  onRadiusKm: (v: number) => void
  siteLimit: number
  onSiteLimit: (v: number) => void
  onFit: () => void
  onRefresh: () => void
  qaQuestion: string
  onQaQuestion: (v: string) => void
  qaBusy: boolean
  onAskQa: () => void
  qaError: string
  qaNote: string
  qaAnswer: string
  locError: string
  /** 扩圈提示：{ 用户选的半径, 原半径内站点数, 实际半径(km) }；null = 没扩 */
  widened: { radiusKm: number; withinOriginal: number; effectiveKm: number } | null
}) {
  const { t } = useI18n()

  return (
    <CardBase style={{ marginBottom: 'var(--wh-space-md)' }}>
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        <Space wrap size={8} align="center">
          <span style={{ fontSize: 12 }}>{t('loc.ctrl.lat')}</span>
          <Input value={latText} onChange={(e) => onLatText(e.target.value)} style={{ width: 130 }} maxLength={20} />
          <span style={{ fontSize: 12 }}>{t('loc.ctrl.lon')}</span>
          <Input value={lonText} onChange={(e) => onLonText(e.target.value)} style={{ width: 130 }} maxLength={20} />
          <Button type="primary" onClick={onApply}>{t('loc.ctrl.apply')}</Button>
          <Button icon={<AimOutlined />} loading={geoBusy} onClick={onUseMyLocation}>{t('loc.ctrl.myLocation')}</Button>
          <span style={{ fontSize: 12 }}>{t('loc.ctrl.radius')}</span>
          <Select
            value={radiusKm}
            onChange={onRadiusKm}
            style={{ width: 96 }}
            options={RADIUS_OPTIONS.map((v) => ({ value: v, label: `${v} km` }))}
          />
          <span style={{ fontSize: 12 }}>{t('loc.ctrl.limit')}</span>
          <Select
            value={siteLimit}
            onChange={onSiteLimit}
            style={{ width: 78 }}
            options={LIMIT_OPTIONS.map((v) => ({ value: v, label: String(v) }))}
          />
          <Button icon={<SearchOutlined />} onClick={onFit}>{t('loc.ctrl.fit')}</Button>
          <Button icon={<ReloadOutlined />} onClick={onRefresh}>{t('loc.ctrl.refresh')}</Button>
        </Space>

        <Space wrap size={8} align="center">
          <LinkOutlined style={{ color: 'var(--wh-teal)' }} />
          <Text style={{ fontSize: 12 }}>{t('loc.qa.label')}</Text>
          <Input
            value={qaQuestion}
            onChange={(e) => onQaQuestion(e.target.value)}
            onPressEnter={onAskQa}
            placeholder={t('loc.qa.placeholder')}
            style={{ width: 380, maxWidth: '100%' }}
            maxLength={500}
          />
          <Button loading={qaBusy} onClick={onAskQa}>{t('loc.qa.ask')}</Button>
          <ExplainIcon text={t('loc.qa.explain')} size="0.9rem" />
        </Space>
        {!!qaError && <Alert type="warning" showIcon message={qaError} />}
        {!!qaNote && <Alert type="info" showIcon message={qaNote} />}
        {!!qaAnswer && (
          <Typography.Paragraph
            style={{ marginBottom: 0, fontSize: 12, whiteSpace: 'pre-wrap' }}
            ellipsis={{ rows: 3, expandable: true, symbol: t('loc.qa.more') }}
          >
            {qaAnswer}
          </Typography.Paragraph>
        )}
        {!!locError && <Alert type="error" showIcon message={locError} />}
        {widened && (
          <Text type="warning" style={{ fontSize: 12 }}>
            {t('loc.warn.widened', {
              r: widened.radiusKm, n: widened.withinOriginal, km: fmtKm(widened.effectiveKm),
            })}
          </Text>
        )}
      </Space>
    </CardBase>
  )
}
