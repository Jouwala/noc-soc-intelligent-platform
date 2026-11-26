/**
 * 号码查询（TEL / MSISDN → VMAX CCA）的访问控制。2026-11-23 新增。
 *
 * 需求：只有指定的人能查电话信息，其他人回固定话术；其余问题一律照常回答。
 *   · 网页助手：按 my_app 登录用户名放行（默认 admin）；
 *   · WhatsApp：按提问人手机号放行（默认 +8615638281623）。
 * 名单在 my_app「系统设置 → 号码查询权限」页里改，存 my_app 的
 * backend/services/phone_query_acl.json，本模块经 GET /api/phone-query-acl 读取，
 * 缓存 PHONE_ACL_TTL_MS（默认 60 秒）—— 页面保存后最多一分钟生效，不用重启 bot。
 *
 * 拦截点只有一个：src/tools.js 的 runTool('query_vmax_cca')。规则层（看到 TEL 就查）
 * 和模型层（模型自己选了这个工具）最终都走那里，所以两条路都拦得住。
 *
 * ⚠️ WhatsApp 群里的提问人常常是 LID（如 112176710811648@lid），不是手机号。
 *   Baileys 6.7 会在 msg.key.participantPn / senderPn 里带上真实号码，优先用它；
 *   拿不到号码时只能按 LID 比 —— 被拒的日志会打印 pn / lid，必要时把 LID 也加进名单。
 *
 * my_app 不可达时：先用上次成功拉到的名单；从没拉到过就用 config.json 的
 * phoneQueryAcl 默认值（仍然是「只放行名单里的人」，不会因为 my_app 挂了而全放开）。
 * 没有 caller 的调用（命令行 npm run ask、单测）不受限 —— 那是本机运维自己在跑。
 */
import { CONFIG } from './config.js';

const TTL_MS = Number(process.env.PHONE_ACL_TTL_MS || 60000);
const FETCH_TIMEOUT_MS = 5000;

const BUILTIN_DEFAULTS = {
  enabled: true,
  web_users: ['admin'],
  whatsapp_numbers: ['+8615638281623'],
  deny_text_zh: '你无权查询该信息，请找admin/zeeshan进行查询',
  deny_text_en: 'You are not authorized to query this information. Please contact admin/zeeshan.',
};

let cache = null;
let cacheAt = 0;

/** 只留数字：'+86 156-3828-1623' / '8615638281623@s.whatsapp.net' / '1121...:12@lid' → 纯数字串 */
export function digitsOf(v) {
  return String(v || '').split('@')[0].split(':')[0].replace(/\D/g, '');
}

function toList(v) {
  if (Array.isArray(v)) return v.map((s) => String(s || '').trim()).filter(Boolean);
  if (typeof v === 'string') return v.split(/[\s,，;；]+/).map((s) => s.trim()).filter(Boolean);
  return [];
}

/** 把 my_app 返回 / config.json 里的配置规整成统一形状。 */
export function normalizeAcl(raw) {
  const d = { ...BUILTIN_DEFAULTS, ...(CONFIG.phoneQueryAcl || {}) };
  const r = raw && typeof raw === 'object' ? raw : {};
  const pick = (k) => (r[k] !== undefined ? r[k] : d[k]);
  return {
    enabled: pick('enabled') !== false,
    webUsers: toList(pick('web_users')).map((s) => s.toLowerCase()),
    whatsappNumbers: toList(pick('whatsapp_numbers')).map(digitsOf).filter(Boolean),
    denyTextZh: String(pick('deny_text_zh') || BUILTIN_DEFAULTS.deny_text_zh),
    denyTextEn: String(pick('deny_text_en') || BUILTIN_DEFAULTS.deny_text_en),
  };
}

/** 读名单（带缓存）。失败不抛：退回上次的名单或默认值。 */
export async function loadPhoneAcl() {
  if (cache && Date.now() - cacheAt < TTL_MS) return cache;
  const base = String(CONFIG.myAppApiBase || '').replace(/\/+$/, '');
  if (base) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const res = await fetch(`${base}/api/phone-query-acl`, {
        signal: ctrl.signal,
        headers: { Accept: 'application/json' },
      });
      if (res.ok) {
        const data = await res.json();
        cache = normalizeAcl(data?.config || data);
        cacheAt = Date.now();
        return cache;
      }
      console.warn(`[phone-acl] my_app 返回 ${res.status}，沿用${cache ? '上次的名单' : ' config.json 默认名单'}`);
    } catch (err) {
      console.warn(`[phone-acl] 读不到 my_app 名单（${err.name === 'AbortError' ? '超时' : err.message}），沿用${cache ? '上次的名单' : ' config.json 默认名单'}`);
    } finally {
      clearTimeout(timer);
    }
  }
  if (cache) return cache;
  return normalizeAcl(null);
}

/**
 * 判定 caller 能否查号码。
 * @param {object} acl     normalizeAcl() 的结果
 * @param {object} [caller] { channel:'web', user } | { channel:'whatsapp', jid, pn }
 * @returns {{allowed:boolean, reason:string}}
 */
export function isPhoneQueryAllowed(acl, caller) {
  if (!caller) return { allowed: true, reason: 'no-caller' };
  if (!acl.enabled) return { allowed: true, reason: 'acl-disabled' };
  if (caller.channel === 'web') {
    const user = String(caller.user || '').trim().toLowerCase();
    if (user && acl.webUsers.includes(user)) return { allowed: true, reason: 'web-user' };
    return { allowed: false, reason: user ? 'web-user-not-listed' : 'web-user-missing' };
  }
  if (caller.channel === 'whatsapp') {
    const ids = [caller.pn, caller.jid].map(digitsOf).filter(Boolean);
    if (ids.some((id) => acl.whatsappNumbers.includes(id))) return { allowed: true, reason: 'whatsapp-number' };
    return { allowed: false, reason: ids.length ? 'whatsapp-number-not-listed' : 'whatsapp-id-missing' };
  }
  return { allowed: false, reason: 'unknown-channel' };
}

export function phoneDenyText(acl, lang) {
  return lang === 'en' ? acl.denyTextEn : acl.denyTextZh;
}

/** 给日志用：说清楚是谁被拒/放行（号码与 LID 都打，方便把 LID 补进名单）。 */
export function describeCaller(caller) {
  if (!caller) return 'cli';
  if (caller.channel === 'web') return `web:${caller.user || '?'}`;
  if (caller.channel === 'whatsapp') return `wa:pn=${digitsOf(caller.pn) || '-'} jid=${caller.jid || '-'}`;
  return String(caller.channel || '?');
}

/** 一步到位：读名单 + 判定。 */
export async function checkPhoneQuery(caller) {
  if (!caller) return { allowed: true, reason: 'no-caller', acl: null };
  const acl = await loadPhoneAcl();
  return { ...isPhoneQueryAllowed(acl, caller), acl };
}
