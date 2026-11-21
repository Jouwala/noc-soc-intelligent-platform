/* ============================================================================
 * NOC 拓扑中心 —— 选中 / 搜索定位
 * ============================================================================
 * 选中状态只有两个 key（设备 / 链路），互斥；搜索定位返回命中的设备 key，
 * 由画布负责把视口带过去。筛选把选中项过滤掉时自动清空，避免抽屉里
 * 还挂着一个已经不在图上的设备。
 * ========================================================================== */
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { NocEdge, NocModel, NocNode } from '../types'

export function useTopologySelection(model: NocModel) {
  const [nodeKey, setNodeKey] = useState<string | null>(null)
  const [edgeKey, setEdgeKey] = useState<string | null>(null)
  /** 每次定位自增，画布据此把视口移到选中点 */
  const [focusToken, setFocusToken] = useState(0)

  const node: NocNode | null = useMemo(
    () => (nodeKey ? model.nodes.find((n) => n.key === nodeKey) ?? null : null),
    [model.nodes, nodeKey],
  )
  const edge: NocEdge | null = useMemo(
    () => (edgeKey ? model.edges.find((e) => e.key === edgeKey) ?? null : null),
    [model.edges, edgeKey],
  )

  useEffect(() => {
    if (nodeKey && !model.nodes.some((n) => n.key === nodeKey)) setNodeKey(null)
    if (edgeKey && !model.edges.some((e) => e.key === edgeKey)) setEdgeKey(null)
  }, [model, nodeKey, edgeKey])

  const selectNode = useCallback((key: string | null) => {
    setNodeKey(key)
    setEdgeKey(null)
  }, [])

  const selectEdge = useCallback((key: string | null) => {
    setEdgeKey(key)
    setNodeKey(null)
  }, [])

  const clear = useCallback(() => { setNodeKey(null); setEdgeKey(null) }, [])

  /** 搜索定位：返回命中的 key（未命中返回 null，由页面提示「无匹配」） */
  const locate = useCallback((query: string): string | null => {
    const q = query.trim().toLowerCase()
    if (!q) return null
    const hit = model.nodes.find((n) =>
      n.name.toLowerCase().includes(q)
      || (n.ip ?? '').toLowerCase().includes(q)
      || n.nativeId.toLowerCase().includes(q))
    if (!hit) return null
    setNodeKey(hit.key)
    setEdgeKey(null)
    // 只有「主动搜索」才把视口带过去；点选节点不打断用户当前的缩放/平移
    setFocusToken((t) => t + 1)
    return hit.key
  }, [model.nodes])

  return { nodeKey, edgeKey, node, edge, focusToken, selectNode, selectEdge, clear, locate }
}
