/* ============================================================================
 * location/answerSite —— 从问答助手答案里取站点，用于补坐标
 * ============================================================================
 * 问答助手（/api/portal-qa/ask）的站点属性答案里带「站号：4067」与站名；
 * 旧版答案不含坐标时，位置页用站号去站点主数据换坐标。找不到返回 undefined，
 * 调用方必须如实提示「没有坐标」，不得猜点。
 * ========================================================================== */
import type { Site } from '../gismap/types'

export function siteFromAnswer(answer: string, sites: Site[]): Site | undefined {
  if (!answer || !sites.length) return undefined
  const idm = answer.match(/(?:站号|site\s*id|site_id)\s*[:：]\s*([0-9]{3,})/i)
  if (idm) {
    const hit = sites.find((x) => String(x.site_id) === idm[1])
    if (hit) return hit
  }
  const name = answer.match(/Site attributes for\s+([^\n]+)/i)?.[1]
    || answer.match(/站点属性[：:]\s*([^\n]+)/)?.[1]
  if (name) {
    const key = name.trim().toLowerCase()
    return sites.find((x) => String(x.site_name || '').trim().toLowerCase() === key)
  }
  return undefined
}
