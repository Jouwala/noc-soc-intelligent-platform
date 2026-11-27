/* ============================================================================
 * 链路健康页 4 张 KPI：状态堆叠 / 最差可用率 / 异常链路 / 数据完整度。
 * 数字全部来自 overview.kpi 或 links 字段选取，前端不重算口径。
 * ========================================================================== */

import { Col, Row } from 'antd'

import { useI18n } from '../../i18n'
import { WH } from '../../theme'
import { CardBase, StatCard } from '../../components/cards'
import { ExplainIcon, ExplainPopover } from '../../components/MetricExplain'
import {
  NA, PrtgKpi, PrtgLink, cleanSensorName, findWorstUptime, fmtInt, fmtNum,
  isCapacityKnown, isSiteMatched, shortDeviceName,
} from './prtgModels'

export interface PrtgHealthKpisProps {
  kpi: PrtgKpi | undefined
  links: PrtgLink[]
  hasOverview: boolean
}

function StatusStackBar({ up, unusual, down, unknown }: {
  up: number; unusual: number; down: number; unknown: number
}) {
  const total = up + unusual + down + unknown
  if (total <= 0) {
    return (
      <div style={{ height: 8, borderRadius: 4, background: 'var(--wh-card-border)', marginTop: 8 }} />
    )
  }
  const seg = (n: number, color: string, key: string) => (
    n <= 0 ? null : (
      <span
        key={key}
        style={{
          display: 'block',
          width: `${(n / total) * 100}%`,
          background: color,
          height: '100%',
        }}
      />
    )
  )
  return (
    <div
      role="img"
      aria-label={`Up ${up}, Unusual ${unusual}, Down ${down}, Unknown ${unknown}`}
      style={{
        display: 'flex',
        height: 8,
        borderRadius: 4,
        overflow: 'hidden',
        marginTop: 8,
        background: 'var(--wh-card-border)',
      }}
    >
      {seg(up, 'var(--wh-ok)', 'up')}
      {seg(unusual, 'var(--wh-warn)', 'unusual')}
      {seg(down, 'var(--wh-danger)', 'down')}
      {seg(unknown, 'var(--wh-text-dim)', 'unknown')}
    </div>
  )
}

/** 状态卡需要自定义堆叠条，StatCard 无 children，故用 CardBase 自绘 */
function StatusKpiCard({
  title, value, explain, up, unusual, down, unknown, hasOverview,
}: {
  title: string
  value: string
  explain: string
  up: number | null
  unusual: number | null
  down: number | null
  unknown: number | null
  hasOverview: boolean
}) {
  const { t } = useI18n()
  return (
    <CardBase bodyStyle={{ padding: 'var(--wh-space-sm) var(--wh-space-md)' }}>
      <div style={{ fontSize: '0.8125rem', color: WH.textMuted, marginBottom: 4 }}>
        {title}
        <span style={{ marginLeft: 4 }}><ExplainIcon text={explain} /></span>
      </div>
      <ExplainPopover text={explain}>
        <div style={{
          color: WH.teal,
          fontWeight: 700,
          fontSize: '1.25rem',
          lineHeight: 1.2,
          fontVariantNumeric: 'tabular-nums',
        }}>
          {value}
        </div>
      </ExplainPopover>
      {hasOverview && (
        <div style={{ marginTop: 4 }}>
          <div style={{ fontSize: 11, color: WH.textMuted }}>{t('prtg.kpi.statusLegend')}</div>
          <StatusStackBar
            up={up ?? 0}
            unusual={unusual ?? 0}
            down={down ?? 0}
            unknown={unknown ?? 0}
          />
        </div>
      )}
    </CardBase>
  )
}

export default function PrtgHealthKpis({ kpi, links, hasOverview }: PrtgHealthKpisProps) {
  const { t } = useI18n()

  const up = kpi?.up ?? null
  const unusual = kpi?.unusual ?? null
  const down = kpi?.down ?? null
  const unknown = kpi?.unknown ?? null

  const worst = findWorstUptime(links)
  const avgUptime = kpi?.avg_uptime_pct ?? null

  const capacityKnown = links.filter(isCapacityKnown).length
  const siteMatched = links.filter(isSiteMatched).length
  const totalLinks = links.length
  const completenessOk = totalLinks > 0 && capacityKnown === totalLinks && siteMatched === totalLinks
  const completenessWarn = totalLinks > 0 && (capacityKnown < totalLinks || siteMatched < totalLinks)

  const statusValue = !hasOverview
    ? NA
    : `${fmtInt(up)} / ${fmtInt(unusual)} / ${fmtInt(down)} / ${fmtInt(unknown)}`

  const worstValue = !hasOverview ? NA : (worst ? fmtNum(worst.uptime_pct, 3) : NA)
  const worstSuffix = hasOverview && worst ? '%' : ''

  const worstName = worst ? cleanSensorName(worst.link.sensor_name) : NA

  const anomalyValue = hasOverview ? fmtInt(kpi?.anomaly_links) : NA
  const completenessValue = (!hasOverview && totalLinks === 0)
    ? NA
    : t('prtg.kpi.completenessValue', {
      cap: capacityKnown,
      site: siteMatched,
      total: totalLinks || 0,
    })

  const worstExplain = worst
    ? t('prtg.explain.worstUptimeCell', {
      value: fmtNum(worst.uptime_pct, 3),
      name: worstName,
      device: shortDeviceName(worst.link.device_name),
      sensorId: worst.link.sensor_id,
    })
    : t('prtg.explain.worstUptime')

  return (
    <Row gutter={[12, 12]}>
      <Col xs={24} sm={12} lg={6}>
        <StatusKpiCard
          title={t('prtg.kpi.status')}
          value={statusValue}
          explain={t('prtg.explain.statusStack')}
          up={up}
          unusual={unusual}
          down={down}
          unknown={unknown}
          hasOverview={hasOverview}
        />
      </Col>

      <Col xs={24} sm={12} lg={6}>
        <StatCard
          title={(
            <span>
              {t('prtg.kpi.worstUptime')}
              <span style={{ marginLeft: 4 }}>
                <ExplainIcon text={t('prtg.explain.worstUptime')} />
              </span>
            </span>
          )}
          value={worstValue}
          suffix={worstSuffix}
          explainText={worstExplain}
          accentColor={worst && worst.uptime_pct < 99 ? WH.warn : WH.teal}
          subText={(
            <>
              <div title={worst?.link.sensor_name || undefined}>{worstName}</div>
              <div>
                {t('prtg.kpi.avgUptimeSmall', {
                  value: avgUptime == null ? NA : `${fmtNum(avgUptime, 3)}%`,
                })}
              </div>
            </>
          )}
        />
      </Col>

      <Col xs={24} sm={12} lg={6}>
        <StatCard
          title={(
            <span>
              {t('prtg.kpi.anomaly')}
              <span style={{ marginLeft: 4 }}>
                <ExplainIcon text={t('prtg.explain.anomaly')} />
              </span>
            </span>
          )}
          value={anomalyValue}
          explainText={t('prtg.explain.anomaly')}
          accentColor={(kpi?.anomaly_links ?? 0) > 0 ? WH.warn : WH.ok}
        />
      </Col>

      <Col xs={24} sm={12} lg={6}>
        <StatCard
          title={(
            <span>
              {t('prtg.kpi.completeness')}
              <span style={{ marginLeft: 4 }}>
                <ExplainIcon text={t('prtg.explain.completeness')} />
              </span>
            </span>
          )}
          value={completenessValue}
          explainText={t('prtg.explain.completeness')}
          accentColor={completenessWarn ? WH.warn : completenessOk ? WH.ok : WH.teal}
          subText={t('prtg.kpi.completenessHint', {
            cap: capacityKnown,
            site: siteMatched,
            total: totalLinks || NA,
          })}
        />
      </Col>
    </Row>
  )
}
