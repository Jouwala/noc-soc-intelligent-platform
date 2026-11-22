/* 详情抽屉里的字段行与小节标题（设备/链路两个抽屉共用，避免两套排版走样） */
import type { ReactNode } from 'react'
import { WH } from '../../../theme'

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div style={{
      fontSize: 12, fontWeight: 600, color: WH.text,
      margin: '12px 0 6px', paddingBottom: 4,
      borderBottom: `1px solid ${WH.cardBorder}`,
    }}>
      {children}
    </div>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '2px 0', minWidth: 0 }}>
      <span style={{ fontSize: 12, color: WH.textDim, width: 92, flexShrink: 0 }}>{label}</span>
      <span style={{ fontSize: 12, color: WH.text, minWidth: 0, wordBreak: 'break-all' }}>{children}</span>
    </div>
  )
}
