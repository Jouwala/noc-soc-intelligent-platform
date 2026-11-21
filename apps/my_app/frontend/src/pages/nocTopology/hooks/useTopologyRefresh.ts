/* ============================================================================
 * NOC 拓扑中心 —— 刷新编排
 * ============================================================================
 * 顶栏「开启刷新」对本页是**原地刷新**（页面已登记进 App.tsx 的
 * AR_INPLACE_REFRESH_PAGES）：外壳广播 `myapp-refresh` DOM 事件，
 * 本 hook 接住后走来源级节流刷新，**不重建地图、不重置缩放与选中**。
 *
 * 手动刷新按钮走 `refreshNow()`（force=true），绕过节流与后端缓存。
 * ========================================================================== */
import { useCallback, useEffect, useRef, useState } from 'react'

export function useTopologyRefresh(
  refresh: (opts?: { force?: boolean }) => Promise<void>,
) {
  const ref = useRef(refresh)
  ref.current = refresh
  const [refreshing, setRefreshing] = useState(false)

  const run = useCallback(async (force: boolean) => {
    setRefreshing(true)
    try {
      await ref.current({ force })
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    const handler = () => { void run(false) }
    document.addEventListener('myapp-refresh', handler)
    return () => document.removeEventListener('myapp-refresh', handler)
  }, [run])

  return {
    refreshing,
    refreshNow: useCallback(() => run(true), [run]),
    refreshSilent: useCallback(() => run(false), [run]),
  }
}
