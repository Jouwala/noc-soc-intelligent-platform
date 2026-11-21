/**
 * 切页首帧占位 + 延后一帧挂载（2026-11-20，治「点了没反应」）。
 *
 * 背景（实测，不是推测）：把 CPU 降 4 倍模拟办公机后，切到任务管理页时
 * **单次主线程阻塞 5.7 秒**（TBT 8.7s，DOM 11,359 节点），NUR 页 4.4 秒
 * （12,240 节点）。这期间浏览器连「按下」都排不上队，用户看到的就是
 * 「点了没反应、网页没有响应、要等待」——而服务器上（i7-14700）同样的页面
 * 只阻塞 0.9 秒，所以「在服务器上试是好的」。
 *
 * 为什么光加骨架没用：React 在同一个任务里把 state 换掉、把页面渲染完，
 * 浏览器一次 paint 都没发生过，骨架根本画不出来。所以本组件做了两件事：
 *
 *   1. `DeferredMount` 先用**一帧 + 一个宏任务**把骨架画到屏幕上，
 *      再去渲染真正的页面。实测 2 帧约 32ms，肉眼不可见，但足以让
 *      「点了没反应」变成「正在加载」。
 *   2. 页面再重，屏幕上也是骨架在动，而不是一张定格不动的旧画面。
 *
 * ⚠ 这里只负责「让用户看到在忙」，不减少实际渲染量 —— 那是各页自己的事
 *   （例如 DataSync 的任务卡分批渲染）。
 * ⚠ 文案由调用方传 `label`（t('common.loading')），本组件不依赖 i18n 上下文，
 *   与 `PageErrorBoundary` 的约定一致。
 */
import { useEffect, useState, type ReactNode } from 'react'

/** 骨架块：宽度/高度走内联，动画走 index.css 的 .wh-pl-* 类。 */
function Block({ w, h, radius = 'var(--wh-radius-sm, 6px)' }: { w: string; h: string; radius?: string }) {
  return <div className="wh-pl-block" style={{ width: w, height: h, borderRadius: radius }} />
}

/** 首帧骨架：顶栏进度条 + 一排卡片 + 一块宽内容，形状贴近绝大多数页面。 */
export function PageLoading({ label }: { label?: string }) {
  return (
    <div className="wh-page-loading" role="status" aria-busy="true" aria-live="polite">
      <div className="wh-pl-bar" aria-hidden="true" />
      <div className="wh-pl-cards" aria-hidden="true">
        {[0, 1, 2, 3].map(i => (
          <div key={i} className="wh-pl-card">
            <Block w="42%" h="0.75rem" />
            <Block w="58%" h="1.6rem" />
            <Block w="76%" h="0.625rem" />
          </div>
        ))}
      </div>
      <div className="wh-pl-panel" aria-hidden="true">
        <Block w="26%" h="0.875rem" />
        <Block w="100%" h="10rem" />
      </div>
      {label ? <span className="wh-pl-label">{label}</span> : null}
    </div>
  )
}

/**
 * 先画一帧骨架，再挂载 children。
 *
 * 时序：commit(骨架) → paint → rAF → setTimeout(0) → setReady → 渲染真正的页面。
 * 用 rAF + 宏任务而不是直接 `setState`：React 在同一个任务内 flush 的话，
 * 浏览器来不及 paint，骨架等于没画。
 */
export function DeferredMount({ children, label }: { children: ReactNode; label?: string }) {
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let raf = 0
    let timer = 0
    raf = requestAnimationFrame(() => {
      timer = window.setTimeout(() => setReady(true), 0)
    })
    return () => {
      if (raf) cancelAnimationFrame(raf)
      if (timer) window.clearTimeout(timer)
    }
  }, [])

  if (!ready) return <PageLoading label={label} />
  return <>{children}</>
}
