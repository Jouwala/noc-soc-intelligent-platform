import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, ReactNode } from 'react'
import api from './api'

interface TaskStatusContextType {
  /** task_id -> 是否正在运行（来自后端真实状态） */
  runningMap: Record<string, boolean>
  isRunning: (taskId: string) => boolean
}

const TaskStatusContext = createContext<TaskStatusContextType>({
  runningMap: {},
  isRunning: () => false,
})

/**
 * 在应用顶层维护任务执行状态，轮询后端 /api/tasks 的 running 字段。
 * 由于 provider 位于路由切换（switch 渲染）之上，running 状态在切换页面后依然存活。
 *
 * 轮询频率（2026-11-15 降频，写法不变只调参数）：忙时 5s、空闲 15s，各带 ±15% 抖动。
 * 起因：每个打开的 SPA 页（含大量 embed 大屏）都挂着一个本 provider，
 * 旧的「忙 2s / 闲 8s」让 /api/tasks/running 占到全站请求量的 95%
 * （2026-11-15 nginx 日志实测：6 小时 17.2 万条 ≈ 8 次/秒，全是这一路）。
 * 降频后 running 徽标的最大滞后从 2s 变 5s，对「点执行后看它转起来」足够；
 * 抖动让各标签页的轮询相位错开，不再同一秒齐射。
 */
const POLL_BUSY_MS = 5000
const POLL_IDLE_MS = 15000
/** ±15% 抖动：多标签页/多块大屏的轮询相位错开，避免同一秒齐射打满单进程后端。 */
const withJitter = (base: number) => Math.round(base * (0.85 + Math.random() * 0.3))

/** 两次轮询的运行状态是否**逐键相同**（键集合与布尔值都一致）。
 *  只比较真值键：后端返回的 map 里通常只有「正在运行」的任务，其余不出现。 */
function sameRunning(a: Record<string, boolean>, b: Record<string, boolean>): boolean {
  const keys = Object.keys(b).filter(k => b[k])
  if (keys.length !== Object.keys(a).filter(k => a[k]).length) return false
  for (const k of keys) if (!a[k]) return false
  return true
}

export function TaskStatusProvider({ children }: { children: ReactNode }) {
  const [runningMap, setRunningMap] = useState<Record<string, boolean>>({})
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      let map: Record<string, boolean> = {}
      try {
        // 只取运行状态（约 1KB）；/tasks 会带上最新执行记录/重试预测等，约 75KB，
        // 而这里只需要一个布尔值，且是每 15s（忙时 5s，±15% 抖动）轮询一次。
        const res = await api.get('/tasks/running')
        map = res.data.running || {}
        // 内容没变就**不要** setState：每次轮询都换一个新对象身份，会在 context 上
        // 触发全站所有消费组件重渲染。任务管理页实测每次重渲染要重建 85 张卡、
        // 1.1 万个节点：办公机档（CPU 降 4 倍）单次 4–14 秒，页面等于一直在冻住
        // —— 这才是「点了没反应、网页没有响应」的真正来源（2026-11-20 实测）。
        if (!cancelled) {
          setRunningMap(prev => (sameRunning(prev, map) ? prev : map))
        }
      } catch {
        // 轮询失败忽略，下次继续
      } finally {
        if (!cancelled) {
          const anyRunning = Object.values(map).some(Boolean)
          // 有任务运行时轮询更频繁（5s），空闲时降低频率（15s），均带抖动
          timerRef.current = setTimeout(poll, withJitter(anyRunning ? POLL_BUSY_MS : POLL_IDLE_MS))
        }
      }
    }
    poll()
    return () => {
      cancelled = true
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  // 回调与 context value 都要稳定：否则 Provider 自己每次渲染都会换 identity，
  // 让消费者白白重渲染（与上面 sameRunning 是同一类浪费）。
  const isRunning = useCallback((taskId: string) => !!runningMap[taskId], [runningMap])
  const value = useMemo(() => ({ runningMap, isRunning }), [runningMap, isRunning])

  return (
    <TaskStatusContext.Provider value={value}>
      {children}
    </TaskStatusContext.Provider>
  )
}

export function useTaskStatus() {
  return useContext(TaskStatusContext)
}
