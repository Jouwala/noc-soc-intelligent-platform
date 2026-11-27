/**
 * HeaderMoreMenu.tsx —— 顶栏右上角「更多设置」菜单
 * ============================================================================
 *
 * 背景（2026-11-27）：顶栏右侧原来平铺 5 个控件 ——「开启刷新」按钮 + 间隔下拉 +
 * 主题 + 语言 + 账号芯片，实测在 1600 宽下占 476px（约顶栏宽度的 30%），把标题
 * 区一路挤窄，视觉上也不整齐。改为「一个 ⋯ 按钮 + 一个面板」，把四类设置全部收进来。
 *
 * 取舍与约定：
 *   - 自动刷新状态不再一眼可见，所以在触发按钮上用一个 teal 圆点表示「本页正在自动刷新」；
 *   - 面板内容全部复用既有能力，不新写逻辑：
 *       自动刷新 → 外部传入的 arEffective / onToggleAutoRefresh / onSelectInterval
 *       主题     → 外部传入的 isDark / onToggleTheme
 *       语言     → 外部传入的 onToggleLocale
 *       账号     → PortalUserMenu 的 inline 模式（改密 / 用户管理 / 鉴权开关 / 退出，
 *                  权限判定与三个弹窗仍只在 PortalUserMenu 里维护一份）
 *   - 面板用 antd Dropdown（portal 到 body）而不是绝对定位：Header 有 overflow:hidden
 *     且高度仅 56px，绝对定位面板会被裁掉（与 PortalUserMenu 同一原因）。
 *   - 文案一律 t('key')，新增键已同时写进 i18n.tsx 的 zh / en 两份字典。
 */
import { useState } from 'react'
import type { CSSProperties } from 'react'
import { Badge, Button, Divider, Dropdown, Select, Switch } from 'antd'
import {
  MoonOutlined,
  MoreOutlined,
  SunOutlined,
  SyncOutlined,
  TranslationOutlined,
} from '@ant-design/icons'
import { useI18n } from '../i18n'
import { WH } from '../theme'
import PortalUserMenu, { type AuthIdentity } from './PortalUserMenu'

export interface ArOption {
  value: number
  label: string
}

export default function HeaderMoreMenu({
  identity,
  onLogout,
  isDark,
  onToggleTheme,
  onToggleLocale,
  arEffective,
  arOptions,
  onToggleAutoRefresh,
  onSelectInterval,
  arSpin,
}: {
  identity: AuthIdentity | null
  onLogout: () => void
  isDark: boolean
  onToggleTheme: () => void
  onToggleLocale: () => void
  /** 当前页生效的自动刷新间隔（0 = 关闭），与顶栏旧控件同源 */
  arEffective: number
  arOptions: ArOption[]
  onToggleAutoRefresh: () => void
  onSelectInterval: (sec: number) => void
  arSpin: boolean
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)

  const rowStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '0.75rem',
    padding: '0.3rem 0',
  }
  const labelStyle: CSSProperties = {
    fontSize: '0.78rem',
    color: 'var(--wh-text-secondary)',
  }

  const panel = (
    <div
      style={{
        // 固定宽度：账号区的「本机免鉴权」说明是一整句长文案，不给宽度会被它撑到 500px+
        width: '17.5rem',
        maxWidth: 'calc(100vw - 2rem)',
        padding: '0.5rem 0.65rem',
        background: 'var(--wh-glass-panel-solid, var(--wh-glass-panel))',
        border: '1px solid var(--wh-glass-border)',
        borderRadius: 'var(--wh-radius-md)',
        boxShadow: 'var(--wh-shadow-3)',
      }}
    >
      {/* 自动刷新：开关 + 间隔 */}
      <div style={rowStyle}>
        <span style={labelStyle}>{t('ar.label')}</span>
        <Switch
          size="small"
          checked={arEffective > 0}
          onChange={() => onToggleAutoRefresh()}
          aria-label={t('ar.label')}
        />
      </div>
      <div style={rowStyle}>
        <span style={labelStyle}>{t('ar.interval')}</span>
        <Select
          size="small"
          aria-label={t('ar.label')}
          value={arEffective}
          onChange={(v: number) => onSelectInterval(v)}
          options={arOptions}
          suffixIcon={<SyncOutlined spin={arSpin} style={{ color: WH.teal }} />}
          style={{ width: '8.5rem' }}
        />
      </div>

      <Divider style={{ margin: '0.35rem 0' }} />

      {/* 主题 / 语言：按钮上直接写「点了会变成什么」，与面板外的旧按钮语义一致 */}
      <div style={rowStyle}>
        <span style={labelStyle}>{t('theme.label')}</span>
        <Button
          size="small"
          icon={isDark ? <SunOutlined /> : <MoonOutlined />}
          onClick={onToggleTheme}
          aria-label={isDark ? t('theme.toLight') : t('theme.toDark')}
        >
          {isDark ? t('theme.light') : t('theme.dark')}
        </Button>
      </div>
      <div style={rowStyle}>
        <span style={labelStyle}>{t('lang.label')}</span>
        <Button
          size="small"
          icon={<TranslationOutlined />}
          onClick={onToggleLocale}
          aria-label={t('lang.switch')}
        >
          {t('lang.switch')}
        </Button>
      </div>

      {/* 账号：复用 PortalUserMenu 的内嵌形态（含改密 / 用户管理 / 鉴权开关 / 退出） */}
      {identity && (
        <>
          <Divider style={{ margin: '0.35rem 0' }} />
          <PortalUserMenu
            identity={identity}
            onLogout={onLogout}
            inline
            onNavigate={() => setOpen(false)}
          />
        </>
      )}
    </div>
  )

  return (
    <Dropdown
      open={open}
      onOpenChange={setOpen}
      trigger={['click']}
      placement="bottomRight"
      dropdownRender={() => panel}
    >
      {/* Dropdown 的子元素用 span 包一层：Badge 作为圆点提示不能直接挂事件 */}
      <span style={{ display: 'inline-flex' }}>
        <Badge dot={arEffective > 0} color={WH.teal} offset={[-4, 4]}>
          <Button
            type="text"
            size="small"
            aria-label={t('header.more')}
            aria-expanded={open}
            icon={<MoreOutlined style={{ color: WH.teal, fontSize: '1.05rem' }} />}
          />
        </Badge>
      </span>
    </Dropdown>
  )
}
