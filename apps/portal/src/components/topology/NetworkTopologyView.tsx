import React, { useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, MapPin, Network, Search } from 'lucide-react';
import { useI18n } from '../../i18n';
import { SECTORS, SITES, type Site } from './topologyData';
import CoreMap from './CoreMap';
import ScatterMap from './ScatterMap';
import PhysicalMap, { type FocusReq } from './PhysicalMap';
import ZoomStage from './ZoomStage';
import TrafficTopologyView from './TrafficTopologyView';
import physicalImg from '../../assets/topology-physical.jpg';
import logicalImg from '../../assets/topology-logical.jpg';

// 网络拓扑视图（Orange Liberia OLIB 光纤网络）：原图（物理/逻辑）+ 骨干示意 + 站点分布 + 站点查询。
// 物理原图上叠加全部 553 个站点点位（按类型着色、半径随缩放补偿、点击可选），
// 这是用户修正版定下的方案——站点直接在原图里找，不再用单独的定位图。
// 站点坐标为原图千分比（0-1000），数据来源与口径见页内说明（默认收起）。
type Tab = 'physical' | 'logical' | 'core' | 'scatter';
type Chip = 'all' | '5g' | 'isp' | 'core';
type View = 'sites' | 'traffic';

// 顶层并列视图切换：站点分布（原有四 tab）/ 流量拓扑（Issue #230）。
function ViewSwitch({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  const { t } = useI18n();
  const items: [View, string][] = [
    ['sites', 'topo.viewSites'],
    ['traffic', 'topo.viewTraffic'],
  ];
  return (
    <div className="inline-flex rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-0.5 shadow-sm">
      {items.map(([v, key]) => (
        <button
          key={v}
          type="button"
          onClick={() => onChange(v)}
          aria-pressed={view === v}
          className={`px-3 py-1.5 rounded-lg text-[13px] transition-colors ${
            view === v
              ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900'
              : 'text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800'
          }`}
        >
          {t(key)}
        </button>
      ))}
    </div>
  );
}

export default function NetworkTopologyView() {
  const { t } = useI18n();
  const [view, setView] = useState<View>('sites');
  const [tab, setTab] = useState<Tab>('physical');
  const [query, setQuery] = useState('');
  const [sector, setSector] = useState('');
  const [chip, setChip] = useState<Chip>('all');
  const [selected, setSelected] = useState<Site | null>(null);
  const [focus, setFocus] = useState<FocusReq | null>(null);
  const [sitesVisible, setSitesVisible] = useState(true);
  const [noteOpen, setNoteOpen] = useState(false);

  const coreCount = useMemo(() => SITES.filter((s) => s.core).length, []);
  const sectorLabel = (code: string) => t(`topo.sector.${code}`);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return SITES.filter(
      (s) =>
        (!q || s.name.toLowerCase().includes(q)) &&
        (!sector || s.sector === sector) &&
        (chip === 'all' || (chip === '5g' && s.is5g) || (chip === 'isp' && s.isp) || (chip === 'core' && s.core))
    );
  }, [query, sector, chip]);

  const locate = (s: Site, mode: 'whole' | 'zoom') => {
    setSelected(s);
    setSitesVisible(true);
    setTab('physical');
    setFocus({ site: s, mode, n: Date.now() });
  };

  const tags = (s: Site) => (
    <>
      {s.core && (
        <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full bg-slate-900 text-white align-middle">
          {t('topo.tagCore')}
        </span>
      )}
      {s.is5g && (
        <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full bg-orange-100 text-orange-700 align-middle">5G</span>
      )}
      {s.isp && (
        <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full bg-indigo-50 text-indigo-700 align-middle">ISP</span>
      )}
    </>
  );

  const hint =
    tab === 'physical'
      ? t('topo.hintPhysical')
      : tab === 'logical'
        ? t('topo.hintLogical')
        : tab === 'core'
          ? t('topo.hintCore')
          : t('topo.hintScatter');

  if (view === 'traffic') {
    return (
      <div className="my-4 sm:my-6 space-y-3">
        <ViewSwitch view={view} onChange={setView} />
        <TrafficTopologyView />
      </div>
    );
  }

  return (
    <div className="my-4 sm:my-6 space-y-3">
      <ViewSwitch view={view} onChange={setView} />
      {/* 头部 */}
      <div className="bg-slate-900 text-white rounded-2xl px-4 sm:px-5 py-4 flex flex-wrap items-center gap-3 shadow-sm">
        <span className="bg-[#ff7900] text-white font-extrabold px-2.5 py-1 rounded-md text-[15px]">orange</span>
        <h2 className="text-base sm:text-lg font-bold flex items-center gap-2">
          <Network className="w-5 h-5 text-[#ff7900]" />
          {t('topo.title')}
        </h2>
        <div className="flex gap-2 flex-wrap ml-auto text-[12px] text-slate-300">
          <span className="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1">
            <b className="text-white text-sm mr-1">{SITES.length}</b>
            {t('topo.statSites')}
          </span>
          <span className="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1">
            <b className="text-white text-sm mr-1">{coreCount}</b>
            {t('topo.statCore')}
          </span>
          <span className="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1">
            <b className="text-white text-sm mr-1">40G / 10G</b>
            {t('topo.statBackbone')}
          </span>
          <span className="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1">
            <b className="text-white text-sm mr-1">2</b>
            {t('topo.statDc')}
          </span>
        </div>
        <p className="w-full text-[12px] text-slate-400">{t('topo.subtitle')}</p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_340px] gap-3 items-start">
        {/* 左：地图卡 */}
        <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden shadow-sm">
          <div className="flex gap-1.5 flex-wrap items-center px-3 pt-3">
            {(
              [
                ['physical', 'topo.tabPhysical'],
                ['logical', 'topo.tabLogical'],
                ['core', 'topo.tabCore'],
                ['scatter', 'topo.tabScatter'],
              ] as [Tab, string][]
            ).map(([v, key]) => (
              <button
                key={v}
                onClick={() => setTab(v)}
                className={`px-3 py-1.5 rounded-full border text-[13px] transition-colors ${
                  tab === v
                    ? 'bg-slate-900 text-white border-slate-900 dark:bg-white dark:text-slate-900'
                    : 'bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800'
                }`}
              >
                {t(key)}
              </button>
            ))}
            {tab === 'physical' && (
              <button
                onClick={() => setSitesVisible((v) => !v)}
                className="ml-auto px-3 py-1.5 rounded-full border border-slate-200 dark:border-slate-700 text-[13px] text-slate-600 dark:text-slate-300"
              >
                {sitesVisible ? t('topo.pointsShown') : t('topo.pointsHidden')}
              </button>
            )}
          </div>

          <div className="mt-2.5 border-t border-slate-100 dark:border-slate-800">
            {tab === 'core' && (
              <ZoomStage
                baseW={1040}
                baseH={540}
                zoomInLabel={t('topo.zoomIn')}
                zoomOutLabel={t('topo.zoomOut')}
                resetLabel={t('topo.zoomReset')}
                fullLabel={t('topo.zoomFull')}
              >
                <CoreMap
                  onSelect={(s) => setSelected(s)}
                  legend={{
                    exist: t('topo.coreExist'),
                    rail: t('topo.coreRail'),
                    p25a: t('topo.coreP25a'),
                    dwdm: t('topo.coreDwdm'),
                  }}
                  distanceNote={t('topo.coreDist')}
                />
              </ZoomStage>
            )}
            {tab === 'scatter' && (
              <ZoomStage
                baseW={1000}
                baseH={863}
                zoomInLabel={t('topo.zoomIn')}
                zoomOutLabel={t('topo.zoomOut')}
                resetLabel={t('topo.zoomReset')}
                fullLabel={t('topo.zoomFull')}
              >
                <ScatterMap selected={selected} onSelect={(s) => setSelected(s)} />
              </ZoomStage>
            )}
            {(tab === 'physical' || tab === 'logical') && (
              <PhysicalMap
                srcImg={tab === 'physical' ? physicalImg : logicalImg}
                selected={selected}
                showSite={tab === 'physical'}
                sitesVisible={sitesVisible}
                onSelectSite={(s) => setSelected(s)}
                focus={focus}
                sectorLabel={selected ? sectorLabel(selected.sector) : ''}
                mmTitle={t('topo.mmTitle')}
                zoomInLabel={t('topo.zoomIn')}
                zoomOutLabel={t('topo.zoomOut')}
                resetLabel={t('topo.zoomReset')}
                fullLabel={t('topo.zoomFull')}
                onClear={() => setSelected(null)}
              />
            )}
          </div>
          <p className="px-3 py-2 text-[12px] text-slate-500 dark:text-slate-400 border-t border-slate-100 dark:border-slate-800">
            {hint}
          </p>

          {/* 图例 */}
          <div className="px-3 pb-3">
            <h4 className="text-[13px] font-semibold mb-1.5">{t('topo.legendTitle')}</h4>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-[12px] text-slate-600 dark:text-slate-300">
              <span className="flex items-center gap-2">
                <span className="w-6 border-t-[5px] rounded border-[#f2c094]" /> {t('topo.lg40')}
              </span>
              <span className="flex items-center gap-2">
                <span className="w-6 border-t-[5px] rounded border-[#f5d90a]" /> {t('topo.lg10')}
              </span>
              <span className="flex items-center gap-2">
                <span className="w-6 border-t-[5px] rounded border-[#3d9bd6]" /> {t('topo.lgRent')}
              </span>
              <span className="flex items-center gap-2">
                <span className="w-6 border-t-[5px] rounded border-[#a8c8e8]" /> {t('topo.lgOngoing')}
              </span>
              <span className="flex items-center gap-2">
                <span className="w-6 border-t-[5px] rounded border-[#7b2d8b]" /> {t('topo.lgDwdm')}
              </span>
              <span className="flex items-center gap-2">
                <span className="w-6 border-t-[5px] rounded border-[#1a9e6b]" /> {t('topo.lgMetro')}
              </span>
              <span className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-[#263238] shrink-0" /> {t('topo.lgSiteNormal')}
              </span>
              <span className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-[#ef4444] shrink-0" /> {t('topo.lgSite5g')}
              </span>
              <span className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-[#0ea5e9] shrink-0" /> {t('topo.lgSiteIsp')}
              </span>
              <span className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-[#7c3aed] shrink-0" /> {t('topo.lgSiteCore')}
              </span>
              <span className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-[#ff7900] ring-2 ring-[#ff7900]/40 shrink-0" /> {t('topo.lgSiteSel')}
              </span>
            </div>
          </div>

          {/* 数据来源与口径说明：默认收起，点击展开（工作区 UI 约定第 10 条） */}
          <div className="border-t border-slate-100 dark:border-slate-800 px-3 py-2">
            <button
              onClick={() => setNoteOpen((v) => !v)}
              className="flex items-center gap-1.5 text-[12.5px] font-medium text-slate-600 dark:text-slate-300"
            >
              {noteOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              {t('topo.noteTitle')}
              <span className="text-slate-400">{noteOpen ? t('topo.collapse') : t('topo.expand')}</span>
            </button>
            {noteOpen && (
              <p className="mt-1.5 text-[12px] leading-relaxed text-slate-500 dark:text-slate-400">{t('topo.noteBody')}</p>
            )}
          </div>
        </section>

        {/* 右：查询栏 */}
        <aside className="space-y-3">
          <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-3 shadow-sm">
            <div className="relative">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t('topo.searchPlaceholder')}
                className="w-full pl-9 pr-3 py-2.5 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-transparent focus:outline-none focus:border-[#ff7900]"
              />
            </div>
            <select
              value={sector}
              onChange={(e) => setSector(e.target.value)}
              className="w-full mt-2.5 px-2.5 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-[13px] bg-white dark:bg-slate-900"
            >
              <option value="">{t('topo.allSectors')}</option>
              {SECTORS.map((code) => (
                <option key={code} value={code}>
                  {sectorLabel(code)}
                </option>
              ))}
            </select>
            <div className="flex gap-1.5 flex-wrap mt-2.5">
              {(
                [
                  ['all', 'topo.fAll'],
                  ['5g', 'topo.f5g'],
                  ['isp', 'topo.fIsp'],
                  ['core', 'topo.fCore'],
                ] as [Chip, string][]
              ).map(([c, key]) => (
                <button
                  key={c}
                  onClick={() => setChip(c)}
                  className={`text-[12px] px-2.5 py-1 rounded-full border transition-colors ${
                    chip === c
                      ? 'bg-[#ff7900] border-[#ff7900] text-white'
                      : 'bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300'
                  }`}
                >
                  {t(key)}
                </button>
              ))}
            </div>
            <p className="text-[12px] text-slate-500 dark:text-slate-400 mt-2.5">
              {t('topo.found', { n: filtered.length, total: SITES.length })}
            </p>
          </section>

          <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden shadow-sm">
            <div className="max-h-[46vh] overflow-auto">
              {filtered.length === 0 && (
                <p className="px-3 py-3 text-[13px] text-slate-500 dark:text-slate-400">{t('topo.noMatch')}</p>
              )}
              {filtered.slice(0, 300).map((s) => (
                <button
                  key={s.name + s.x + s.y}
                  onClick={() => locate(s, 'whole')}
                  className={`w-full text-left px-3 py-2 border-t border-slate-100 dark:border-slate-800 text-[13px] hover:bg-orange-50 dark:hover:bg-slate-800 ${
                    selected === s ? 'bg-orange-50 dark:bg-slate-800' : ''
                  }`}
                >
                  <b>{s.name}</b>
                  {tags(s)}
                  <span className="block text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                    {sectorLabel(s.sector)} · {t('topo.origPos')} {(s.x / 10).toFixed(1)}%, {(s.y / 10).toFixed(1)}%
                  </span>
                </button>
              ))}
            </div>
          </section>

          <section className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-3 shadow-sm text-[13px]">
            {!selected && (
              <>
                <h3 className="text-[15px] font-bold mb-1.5">{t('topo.detailTitle')}</h3>
                <p className="text-slate-500 dark:text-slate-400">{t('topo.detailEmpty')}</p>
              </>
            )}
            {selected && (
              <>
                <h3 className="text-[16px] font-bold">
                  {selected.name}
                  {tags(selected)}
                </h3>
                <p className="text-slate-500 dark:text-slate-400 mt-1.5">
                  {t('topo.sectorLabel')}：
                  <b className="text-slate-700 dark:text-slate-200 font-semibold">{sectorLabel(selected.sector)}</b>
                </p>
                <p className="text-slate-500 dark:text-slate-400 mt-1">
                  {t('topo.origPos')}：
                  <b className="text-slate-700 dark:text-slate-200 font-semibold">
                    {(selected.x / 10).toFixed(1)}% × {(selected.y / 10).toFixed(1)}%
                  </b>
                </p>
                <p className="text-slate-500 dark:text-slate-400 mt-1">
                  {t('topo.typeLabel')}：
                  <b className="text-slate-700 dark:text-slate-200 font-semibold">
                    {selected.core ? t('topo.typeCore') : selected.isp ? t('topo.typeIsp') : t('topo.typeAccess')}
                  </b>
                  {selected.is5g ? ` · ${t('topo.tag5gNote')}` : ''}
                </p>
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  <button
                    onClick={() => locate(selected, 'whole')}
                    className="bg-[#ff7900] text-white px-3 py-1.5 rounded-lg text-[13px] flex items-center gap-1"
                  >
                    <MapPin className="w-3.5 h-3.5" />
                    {t('topo.locateBtn')}
                  </button>
                  <button
                    onClick={() => locate(selected, 'zoom')}
                    className="px-3 py-1.5 rounded-lg text-[13px] border border-slate-200 dark:border-slate-700"
                  >
                    {t('topo.zoomBtn')}
                  </button>
                  <button
                    onClick={() => setTab('scatter')}
                    className="px-3 py-1.5 rounded-lg text-[13px] border border-slate-200 dark:border-slate-700"
                  >
                    {t('topo.scatterBtn')}
                  </button>
                </div>
                <p className="text-slate-500 dark:text-slate-400 mt-2">{t('topo.detailNote')}</p>
              </>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
