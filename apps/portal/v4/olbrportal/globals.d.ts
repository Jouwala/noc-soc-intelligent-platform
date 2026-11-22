// ============================================================================
// v1/olbrportal 手写静态壳的外部全局声明
// ----------------------------------------------------------------------------
// olbr.js 是 IIFE 全局脚本，运行时由 /MsPortal/assets/ 下的几个脚本往 window
// 上注入对象/函数。tsc 的 DOM lib 不认识这些业务全局，会在 checkJs 时报
// `Property 'X' does not exist on type 'Window'`。这里集中声明，让守卫只抓
// 真正的 bug（语法错、引用未声明的本地变量），不被「外部注入的全局」噪声淹没。
//
// 每条都标注来源脚本，改对应脚本时回来核对签名是否还匹配。
// ============================================================================

// olbr.js 多处给 `new Error(...)` 挂自定义 `.code`（'locked'/'bad'/'net'/'duplicate'
// 等）做错误分流。标准 Error 没有这个属性，这里扩展它，避免 3 处 @ts-expect-error。
interface Error {
  code?: string;
}

/** /MsPortal/assets/data.js 注入：51 个看板 / 9 分组 / QC 定义 / 升级机制 */
declare interface Window {
  MS_PORTAL_DATA: any;
  /** /MsPortal/assets/access.js 注入：权限判定 + 跨门户看板 id 映射 */
  MSPortalAccess: {
    canSee: (view: string, item?: any) => boolean;
    dashboardAccessId?: (it: any) => string;
  } | undefined;
  /** /MsPortal/assets/terms.js 注入：中英文词条映射查表 */
  msPortalTerm: ((kind: string, zh: string) => string) | undefined;
  /** /MsPortal/assets/terms.js 注入：英文字段含汉字时回查映射表（en, kind, zh 三参） */
  msPortalEnOf: ((en: string, kind: string, zh: string) => string) | undefined;
  /** /MsPortal/assets/qcDrill.drag.js 注入：下钻弹窗的拖动与定位 */
  QcDrill: { position: (anchor: Element | null) => void } | undefined;
  /** /MsPortal/assets/excelReport.js 注入：导出 Excel（UNIFIED_EXCEL_V1 格式） */
  MsExcelReport: any;
  /**
   * 本页自己的纯数据母本，由 assets/olbr-data.js（在本页 olbr.js 之前加载）注入。
   * 2026-09-25 从 olbr.js 拆分出来的 i18n 字典 / 图标 / layout / QC 阈值 / KPI 规格。
   * 主文件只读不写：改这些常量请改 olbr-data.js，不要在这里补写。
   */
  OLBR_DATA: any;
  /**
   * 团队门户 assets/team-scope.js 注入的作用域查询引擎。
   * /v3/olbrportal/ 不引该脚本，运行时没有这个属性；olbr.js 的 wantCxortGroup()
   * 读 window.OLBR_SCOPE，缺了就 fail-closed（不取 CXO RealTime）。
   * 本声明只让 checkJs 认识这个可选全局，不引入任何运行时代码。
   * 签名与 v3/team_portal/globals.d.ts 的 OLBR_SCOPE 对齐；此处多一个 undefined，
   * 因为上游页根本不赋值。
   */
  OLBR_SCOPE: {
    teamId: string;
    team: { id: string; zh: string; en: string } | null;
    isTeamPortal: boolean;
    matrix: { [domain: string]: { [sub: string]: string[] } };
    ok: (domain: string, sub?: string) => boolean;
    subs: (domain: string) => string[];
    ossSlotOf: (item: any) => string;
    ossSlots: () => string[];
    viewGroups: () => string[];
    mapPages: () => string[];
    qaTabs: () => string[];
    reportIds: () => string[];
    hasComplaint: () => boolean;
    hasEscalation: () => boolean;
    hasFeedback: () => boolean;
  } | null | undefined;
}
