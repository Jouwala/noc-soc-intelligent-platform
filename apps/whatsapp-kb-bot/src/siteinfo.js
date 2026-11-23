/**
 * 站点基本信息（Basic Info）渲染 —— 内容对齐 GIS 页面 ?page=gis 下钻抽屉的 Basic Info。
 *
 * ## 为什么单独一个模块
 *
 * 数据源在 scripts/site_basic_info.py（随告警桥接脚本同进程查出，见 sitealarms.js），
 * 但「查到什么字段、怎么摆进聊天窗口」是纯渲染职责，与告警状态无关：
 *   · sitealarms.js 回答「这个站**现在有没有告警**」（eFlow 实时活动告警）；
 *   · 本模块回答「这个站**是什么**」（站点主数据，GIS 下钻同口径）。
 * 两者是不同事实，分开渲染、在 tools.js 组合层拼到一条回复里。
 *
 * ## 三条纪律（与 siteattr.js 同源）
 *
 *   ① 字段**只列有值的**：没有就是没有，不显示 0、不编造（取不到显示 —）；
 *   ② 同名多条**全部并列**（库里 Palm Bay 有 3067/4067 两条，各是一套独立记录），
 *      按站号分组编号，不合并、不挑一条；
 *   ③ 查无此站**如实说**，列相近站名供核对，并 @ 数据维护人更新主数据 ——
 *      **绝不**用告警行里的站号/区域冒充站点主数据（那是另一种事实）。
 *
 * ## 数据来源口径
 *
 * site_info.source = 'live'（PG 生产库，与 GIS 页面同源）/ 'sqlite'（本地副本，有延迟）
 * / 'none'（查询失败）。降级和失败都要在文案里标注，不能让用户以为是实时值。
 */

/** 数据维护人：站点主数据查不到时 @ 他更新。用户指定以 @+231 77 236 0781 的形式出现。 */
export const SITE_DATA_MAINTAINER = '+231 77 236 0781';

const T = {
  zh: {
    header: '站点信息（Basic Info，站点主数据）',
    recordHead: (i) => `【站点 ${i}】`,
    labels: {
      site_id: '站号',
      site_name: '站名',
      region: '区域',
      province_county: '省/县',
      network_type: '网络制式',
      trm_type: '传输类型',       // GIS 抽屉标签是 Site Type，实际取 trm_type
      site_caregory: '站点等级',  // NORMAL / COOL / ERA
      site_type: '机房形态',
      vip_site: 'VIP',
      fme_name: '维护人',
      fme_contact_1: '联系电话',
      coords: '坐标',
    },
    vipYes: '是',
    vipNo: '否',
    notFound: (site) => `站点主数据里查不到「${site}」。`,
    candidates: (names) => `库中相近的站名：${names.join('、')}（请核对拼写，或按正式站名重问）。`,
    updateHint: (contact) => `如需新增/更正该站，请 @${contact} 更新站点数据库。`,
    sourceLive: '来源：站点主数据库（实时）',
    sourceLocal: '来源：本地站点库副本（可能有延迟，非实时）',
    error: (detail) => `站点信息查询失败${detail ? `：${detail}` : ''}。`,
  },
  en: {
    header: 'Site info (Basic Info, site master data)',
    recordHead: (i) => `【Site ${i}】`,
    labels: {
      site_id: 'Site ID',
      site_name: 'Site name',
      region: 'Region',
      province_county: 'Province/County',
      network_type: 'Network type',
      trm_type: 'Transmission',
      site_caregory: 'Site category',
      site_type: 'Site type',
      vip_site: 'VIP',
      fme_name: 'Maintenance owner',
      fme_contact_1: 'Contact',
      coords: 'Coordinates',
    },
    vipYes: 'Yes',
    vipNo: 'No',
    notFound: (site) => `No site "${site}" in the site master database.`,
    candidates: (names) => `Similar site names in the database: ${names.join(', ')} (check the spelling, or ask again with the official name).`,
    updateHint: (contact) => `To add or correct this site, please @${contact} update the site database.`,
    sourceLive: 'Source: site master database (live)',
    sourceLocal: 'Source: local site database copy (may be delayed, not live)',
    error: (detail) => `Site info lookup failed${detail ? `: ${detail}` : ''}.`,
  },
};

/** Yes/No、Y/N、1/0 → 是/否；无法识别的值原样返回，不猜。 */
function vipText(v, t) {
  const s = String(v ?? '').trim().toLowerCase();
  if (!s) return '';
  if (['yes', 'y', '1', 'true'].includes(s)) return t.vipYes;
  if (['no', 'n', '0', 'false'].includes(s)) return t.vipNo;
  return String(v).trim();
}

/** 坐标：两者都有才显示（缺一个显示半截坐标没有意义）。 */
function coordsText(r) {
  const lat = Number(r.latitude);
  const lon = Number(r.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return '';
  return `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
}

/**
 * 渲染一条站点记录（只列有值的字段）。
 * @returns {string[]} 行数组
 */
function renderRecord(r, i, multi, t) {
  const lines = [];
  if (multi) {
    lines.push(t.recordHead(i + 1));
  }
  const put = (key, value) => {
    const v = String(value ?? '').trim();
    if (v) lines.push(`  ${t.labels[key]}: ${v}`);
  };
  put('site_name', r.site_name);
  put('site_id', r.site_id);
  put('region', r.region);
  put('province_county', r.province_county);
  put('network_type', r.network_type);
  put('trm_type', r.trm_type);
  put('site_caregory', r.site_caregory);
  put('site_type', r.site_type);
  if (r.vip_site != null && String(r.vip_site).trim()) {
    put('vip_site', vipText(r.vip_site, t));
  }
  const fme = String(r.fme_name ?? '').trim();
  const contact = String(r.fme_contact_1 ?? '').trim();
  if (fme || contact) put('fme_name', [fme, contact].filter(Boolean).join(' / '));
  const coords = coordsText(r);
  if (coords) put('coords', coords);
  return lines;
}

/**
 * 把 scripts/site_basic_info.py 的输出渲染成聊天文本块。
 *
 * @param {object|null} si   site_info（告警 JSON 里的字段）；null/缺失返回空串
 * @param {'zh'|'en'} lang
 * @returns {string} 渲染后的文本（不含前后空行；调用方决定间距）
 */
export function renderSiteInfo(si, lang = 'zh') {
  if (!si || typeof si !== 'object') return '';
  const t = T[lang] || T.zh;
  const lines = [];
  const isLocal = si.source === 'sqlite';

  if (si.error && !si.found) {
    // 查询失败：如实说查不了，仍给出更新提醒（用户要的兜底动作）。
    lines.push(t.header);
    lines.push(t.error(si.error));
    lines.push(t.updateHint(SITE_DATA_MAINTAINER));
    return lines.join('\n');
  }

  if (!si.found) {
    lines.push(t.header);
    lines.push(t.notFound(si.query || ''));
    const cands = Array.isArray(si.candidates) ? si.candidates.filter(Boolean) : [];
    if (cands.length) lines.push(t.candidates(cands));
    lines.push(t.updateHint(SITE_DATA_MAINTAINER));
    return lines.join('\n');
  }

  const records = Array.isArray(si.records) ? si.records : [];
  if (!records.length) return '';
  lines.push(t.header);
  const multi = records.length > 1;
  records.forEach((r, i) => {
    if (i > 0) lines.push('');       // 多条之间空一行，聊天窗口里分得清
    lines.push(...renderRecord(r, i, multi, t));
  });
  if (multi) {
    lines.push(`  (${lang === 'en' ? 'multiple records with the same name, listed separately' : '同名多条记录，已分别列出'})`);
  }
  lines.push(isLocal ? t.sourceLocal : t.sourceLive);
  return lines.join('\n');
}

export const _internal = { T, renderRecord, vipText, coordsText };
