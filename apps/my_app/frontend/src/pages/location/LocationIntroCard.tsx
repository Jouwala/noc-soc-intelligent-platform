/* ============================================================================
 * location/LocationIntroCard —— 位置页的标题 + 口径说明卡
 * ============================================================================
 * 2026-11-23 用户要求把「Location analysis 组件」放到页面最下面一行：
 * 地图、搜索窗、订阅者轨迹是主内容，标题与口径说明作为页脚式收尾。
 * 从 pages/LocationMap.tsx 拆出，避免主文件超过 500 行（AGENTS 三.2）。
 *
 * 口径说明仍走项目的 CalcChainNote（默认收起）；数据层加载失败走 warning 口
 * 常驻显示 —— 这是「异常」不是「解释」，不随收起隐藏。
 * ========================================================================== */
import { Alert } from 'antd'
import { EnvironmentOutlined } from '@ant-design/icons'

import { useI18n } from '../../i18n'
import { WH } from '../../theme'
import { CardBase } from '../../components/cards'
import CalcChainNote from '../../components/CalcChainNote'

export default function LocationIntroCard({ layerErrors }: { layerErrors: string[] }) {
  const { t } = useI18n()

  return (
    <CardBase style={{ marginBottom: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <EnvironmentOutlined style={{ fontSize: 18 }} />
        <span style={{ fontSize: 16, fontWeight: 700, color: WH.text }}>{t('loc.title')}</span>
        <span style={{ fontSize: 12, color: WH.textMuted }}>{t('loc.subtitle')}</span>
      </div>
      <div style={{ marginTop: 'var(--wh-space-sm)' }}>
        <CalcChainNote
          title={t('loc.calc.title')}
          sections={[
            { title: t('loc.calc.posTitle'), lines: [t('loc.calc.pos')] },
            { title: t('loc.calc.ringTitle'), lines: [t('loc.calc.ring')] },
            { title: t('loc.calc.linkTitle'), lines: [t('loc.calc.link')] },
            { title: t('loc.calc.subTitle'), lines: [t('loc.calc.sub')] },
          ]}
          warning={layerErrors.length ? (
            <Alert
              type="warning" showIcon style={{ marginBottom: 6 }}
              message={t('loc.warn.layers')}
              description={layerErrors.join('；')}
            />
          ) : undefined}
        />
      </div>
    </CardBase>
  )
}
