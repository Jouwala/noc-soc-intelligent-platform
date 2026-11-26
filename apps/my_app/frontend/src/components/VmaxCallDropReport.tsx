/* ============================================================================
 * VmaxCallDropReport —— 数据通话被电路域来电打断
 * ============================================================================
 * 两个诊断页共用。输入是 analyzeCallDrop 的结果，不自己取数。
 * 复制按钮带 command="--copy"。部分浏览器有 CommandEvent 但点击并不发
 * command，所以 click 始终挂着；两种事件 400ms 内只处理第一次。
 * ========================================================================== */

import { useEffect, useId, type ButtonHTMLAttributes } from 'react'
import { CheckOutlined, CloseOutlined, CopyOutlined } from '@ant-design/icons'

import { useI18n } from '../i18n'
import { renderI18n } from '../renderI18n'
import { CardBase } from './cards'
import { ExplainIcon, ExplainPopover } from './MetricExplain'
import type { CallDropReport } from '../pages/vmaxCallDrop'
import './VmaxCallDropReport.css'

const timers = new WeakMap<HTMLButtonElement, number>()
let lastMessage = ''

function legacyCopy(text: string): boolean {
  const previous = document.activeElement
  const field = Object.assign(document.createElement('textarea'), { value: text })
  field.setAttribute('readonly', '')
  field.style.cssText = 'position:fixed;inset-block-start:0;opacity:0'
  document.body.append(field)
  field.select()
  try {
    return document.execCommand('copy')
  } finally {
    field.remove()
    if (previous instanceof HTMLElement) previous.focus({ preventScroll: true })
  }
}

function reportCopy(button: HTMLButtonElement, ok: boolean, okText: string, badText: string) {
  const tip = button.nextElementSibling
  if (!(tip instanceof HTMLElement)) return
  window.clearTimeout(timers.get(button))
  button.removeAttribute('data-copied')
  button.removeAttribute('data-failed')
  let message = ok ? okText : badText
  if (message === lastMessage) message += '\u00A0'
  lastMessage = message
  tip.textContent = ''
  void button.offsetWidth
  button.setAttribute(ok ? 'data-copied' : 'data-failed', '')
  requestAnimationFrame(() => { tip.textContent = message })
  timers.set(button, window.setTimeout(() => {
    button.removeAttribute('data-copied')
    button.removeAttribute('data-failed')
    tip.textContent = ''
  }, 2000))
}

function copyTarget(
  button: HTMLButtonElement,
  target: HTMLElement,
  okText: string,
  badText: string,
) {
  const text = target.innerText.trim()
  if (!text) return
  // writeText 是异步的，拒绝之后用户点击已经失效，再 execCommand 必然失败。
  // 所以在本次点击里同步写一份；异步接口成功时两边内容相同。
  const legacyOk = legacyCopy(text)
  const write = navigator.clipboard?.writeText(text)
  if (!write) {
    reportCopy(button, legacyOk, okText, badText)
    return
  }
  write.then(
    () => reportCopy(button, true, okText, badText),
    () => reportCopy(button, legacyOk, okText, badText),
  )
}

const dash = (value: string) => value || '—'

export default function VmaxCallDropReport({ report }: { report: CallDropReport }) {
  const { t } = useI18n()
  const reactId = useId()
  const bodyId = `calldrop-body-${reactId.replace(/:/g, '')}`
  const captionId = `calldrop-caption-${reactId.replace(/:/g, '')}`
  const colLeave = t('vmaxq.calldrop.col.leaveCell')
  const colBack = t('vmaxq.calldrop.col.back')
  const colBackCell = t('vmaxq.calldrop.col.backCell')
  const colAway = t('vmaxq.calldrop.col.away')
  const colKind = t('vmaxq.calldrop.col.kind')
  const colBearer = t('vmaxq.calldrop.col.bearer')

  useEffect(() => {
    const okText = t('vmaxq.calldrop.copied')
    const badText = t('vmaxq.calldrop.copyFail')
    const seen = new WeakMap<HTMLButtonElement, number>()
    const run = (button: HTMLButtonElement, target: HTMLElement | null) => {
      const now = Date.now()
      if (now - (seen.get(button) || 0) < 400) return
      seen.set(button, now)
      if (target) void copyTarget(button, target, okText, badText)
    }
    const onClick = (event: MouseEvent) => {
      const el = event.target
      if (!(el instanceof Element)) return
      const button = el.closest('button[command="--copy"]')
      if (!(button instanceof HTMLButtonElement)) return
      const id = button.getAttribute('commandfor')
      run(button, id ? document.getElementById(id) : null)
    }
    const onCommand = (event: Event) => {
      const command = (event as Event & { command?: string; source?: EventTarget | null }).command || ''
      if (command !== '--copy') return
      const source = (event as Event & { source?: EventTarget | null }).source
      // 规范里 target 是按钮、source 是 commandfor 指向的正文；指引稿写反了。两种都认。
      const button = event.target instanceof HTMLButtonElement
        ? event.target
        : (source instanceof HTMLButtonElement ? source : null)
      const body = event.target instanceof HTMLButtonElement
        ? (source instanceof HTMLElement ? source : null)
        : (event.target instanceof HTMLElement ? event.target : null)
      if (button) run(button, body)
    }
    document.addEventListener('click', onClick)
    document.addEventListener('command', onCommand, true)
    return () => {
      document.removeEventListener('click', onClick)
      document.removeEventListener('command', onCommand, true)
    }
  }, [t])

  const quoted = (label: string) => `"${label.replace(/"/g, '')}"`

  return (
    <CardBase
      level={report.cycles.length ? 'warn' : 'ok'}
      title={(
        <span style={{ fontSize: '0.8125rem', fontWeight: 600 }}>
          {t('vmaxq.calldrop.title')} <ExplainIcon text={t('vmaxq.calldrop.explain')} />
        </span>
      )}
      extra={(
        <span className="vmax-calldrop-copy">
          <button
            type="button"
            {...({ command: '--copy', commandFor: bodyId } as ButtonHTMLAttributes<HTMLButtonElement>)}
          >
            <span className="ico-copy" aria-hidden="true"><CopyOutlined /></span>
            <span className="ico-ok" aria-hidden="true"><CheckOutlined /></span>
            <span className="ico-bad" aria-hidden="true"><CloseOutlined /></span>
            {t('vmaxq.calldrop.copy')}
          </button>
          <span className="vmax-calldrop-tip" role="status" />
        </span>
      )}
    >
      <div id={bodyId}>
        <p style={{ margin: 0 }}>{renderI18n(t, report.headline)}</p>
        {report.notes.length > 0 && (
          <ul className="vmax-calldrop-notes">
            {report.notes.map((note, i) => (
              <li key={`${note.key}-${i}`}>{renderI18n(t, note)}</li>
            ))}
          </ul>
        )}
        {report.cycles.length > 0 && (
          <div
            className="vmax-calldrop-wrap"
            tabIndex={0}
            role="region"
            aria-labelledby={captionId}
          >
            <table style={{
              ['--cd-2' as string]: quoted(colLeave),
              ['--cd-3' as string]: quoted(colBack),
              ['--cd-4' as string]: quoted(colBackCell),
              ['--cd-5' as string]: quoted(colAway),
              ['--cd-6' as string]: quoted(colKind),
              ['--cd-7' as string]: quoted(colBearer),
            }}>
              <caption id={captionId}>{t('vmaxq.calldrop.caption')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('vmaxq.calldrop.col.leave')}</th>
                  <th scope="col">{colLeave}</th>
                  <th scope="col">{colBack}</th>
                  <th scope="col">{colBackCell}</th>
                  <th scope="col">
                    {colAway} <ExplainIcon text={t('vmaxq.calldrop.explainAway')} size={11} />
                  </th>
                  <th scope="col">{colKind}</th>
                  <th scope="col">{colBearer}</th>
                </tr>
              </thead>
              <tbody>
                {report.cycles.map((cycle) => (
                  <tr key={`${cycle.leaveAt}-${cycle.leaveCell}`}>
                    <th scope="row">{cycle.leaveAt}</th>
                    <td title={cycle.leaveCell}>{dash(cycle.leaveCell)}</td>
                    <td>{dash(cycle.returnAt)}</td>
                    <td title={cycle.returnCell}>{dash(cycle.returnCell)}</td>
                    <td>
                      {cycle.awaySec == null ? '—' : (
                        <ExplainPopover text={t('vmaxq.calldrop.explainAway')}>
                          <span>{t('vmaxq.calldrop.away', { n: cycle.awaySec })}</span>
                        </ExplainPopover>
                      )}
                    </td>
                    <td>{t(cycle.kind === 'mt' ? 'vmaxq.calldrop.kind.mt' : 'vmaxq.calldrop.kind.mo')}</td>
                    <td>{dash(cycle.bearerCause)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </CardBase>
  )
}
