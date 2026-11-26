// Settings「号码查询权限」页签（2026-11-23）
// 谁能在 WhatsApp 机器人 / 门户问答助手里查电话信息（TEL + 号码 → VMAX CCA）。
// 其余问题不受影响。后端：backend/routers/phone_query_acl.py；机器人每 60 秒读一次名单。
import { useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Input, message, Row, Select, Space, Switch, Typography } from 'antd'
import { SaveOutlined } from '@ant-design/icons'
import api from '../api'
import { useI18n } from '../i18n'
import { WH } from '../theme'

const { Text, Paragraph } = Typography
const { TextArea } = Input

interface PhoneQueryAcl {
  enabled: boolean
  web_users: string[]
  whatsapp_numbers: string[]
  deny_text_zh: string
  deny_text_en: string
}

export default function PhoneQueryAclSettings() {
  const { locale } = useI18n()
  const zh = locale === 'zh'

  const L = {
    title: zh ? '号码查询权限' : 'Phone Query Access',
    subtitle: zh
      ? '「TEL + 手机号」会实时查询 VMAX CCA 的用户信息（IMSI/IMEI/机型/流量/驻留小区）。只有下面名单里的人能查，其他人会收到「无权限」回复；其余问题照常回答。保存后约 1 分钟内生效，无需重启机器人。'
      : '"TEL + phone number" queries live VMAX CCA subscriber data (IMSI/IMEI/model/traffic/cells). Only the people listed below can run it; everyone else gets the "not authorized" reply. Other questions are unaffected. Changes take effect within about 1 minute, no bot restart needed.',
    enabled: zh ? '启用号码查询权限控制' : 'Enable phone query access control',
    enabledOffHint: zh ? '已关闭：所有人都能查电话信息' : 'Off: everyone can query phone information',
    webUsers: zh ? '门户问答助手：允许查询的登录用户名' : 'Portal assistant: allowed login usernames',
    webUsersHint: zh ? '输入用户名后按回车添加，不区分大小写，如 admin' : 'Type a username and press Enter; case-insensitive, e.g. admin',
    waNumbers: zh ? 'WhatsApp 机器人：允许查询的手机号' : 'WhatsApp bot: allowed phone numbers',
    waNumbersHint: zh
      ? '含国家码，如 +8615638281623（+、空格、横线可写可不写）。群里若识别不到号码，机器人日志里被拒的那行会打印 pn / jid，可把其中的 LID 数字也加进来。'
      : 'Include the country code, e.g. +8615638281623 (+, spaces and dashes optional). If the bot cannot see a number in a group, the "denied" log line shows pn / jid; you can add that LID number here too.',
    denyZh: zh ? '无权限时的回复（中文提问）' : 'Denied reply (Chinese questions)',
    denyEn: zh ? '无权限时的回复（英文提问）' : 'Denied reply (English questions)',
    readonly: zh
      ? '你没有修改权限：只有管理员（admin 角色）或本机访问可以修改名单。'
      : 'Read-only: only administrators (admin role) or local access can change these lists.',
    save: zh ? '保存' : 'Save',
    saved: zh ? '已保存，约 1 分钟内生效' : 'Saved, effective within about 1 minute',
    saveFailed: zh ? '保存失败' : 'Save failed',
    loadFailed: zh ? '加载配置失败' : 'Failed to load config',
    emptyWarn: zh ? '名单为空：这一侧将没有任何人能查电话信息' : 'Empty list: nobody on this side will be able to query phone information',
  }

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [editable, setEditable] = useState(false)
  const [enabled, setEnabled] = useState(true)
  const [webUsers, setWebUsers] = useState<string[]>([])
  const [waNumbers, setWaNumbers] = useState<string[]>([])
  const [denyZh, setDenyZh] = useState('')
  const [denyEn, setDenyEn] = useState('')

  const inputStyle = {
    background: 'rgba(0,0,0,0.3)',
    border: '1px solid rgba(0, 217, 255,0.25)',
    color: WH.text,
  }

  const applyConfig = (c: PhoneQueryAcl) => {
    setEnabled(c.enabled !== false)
    setWebUsers(c.web_users || [])
    setWaNumbers(c.whatsapp_numbers || [])
    setDenyZh(c.deny_text_zh || '')
    setDenyEn(c.deny_text_en || '')
  }

  const load = () => {
    setLoading(true)
    setError('')
    api.get('/phone-query-acl')
      .then((res) => {
        applyConfig(res.data.config as PhoneQueryAcl)
        setEditable(!!res.data.editable)
      })
      .catch(() => setError(L.loadFailed))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() /* eslint-disable-line react-hooks/exhaustive-deps */ }, [])

  const save = () => {
    setSaving(true)
    const patch: PhoneQueryAcl = {
      enabled,
      web_users: webUsers.map((s) => s.trim()).filter(Boolean),
      whatsapp_numbers: waNumbers.map((s) => s.trim()).filter(Boolean),
      deny_text_zh: denyZh.trim(),
      deny_text_en: denyEn.trim(),
    }
    api.put('/phone-query-acl', patch)
      .then((res) => {
        applyConfig(res.data.config as PhoneQueryAcl)
        message.success(L.saved)
      })
      .catch((e) => {
        message.error(L.saveFailed + ': ' + (e?.response?.data?.detail || ''))
      })
      .finally(() => setSaving(false))
  }

  const labelStyle = { color: WH.textMuted, fontSize: '0.75rem' }
  const hintStyle = { color: WH.textDim, fontSize: '0.6875rem' }

  return (
    <div style={{ maxWidth: '62rem' }}>
      <Card
        size="small"
        loading={loading}
        title={<Text style={{ color: WH.text, fontSize: '0.875rem' }}>{L.title}</Text>}
        styles={{ header: { background: 'var(--wh-page-bg)' }, body: { background: WH.bg } }}
      >
        <Paragraph style={{ color: WH.textMuted, fontSize: '0.75rem' }}>{L.subtitle}</Paragraph>
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: '0.75rem' }} />}
        {!loading && !editable && (
          <Alert type="warning" showIcon message={L.readonly} style={{ marginBottom: '0.75rem' }} />
        )}

        <div style={{ marginBottom: '0.75rem' }}>
          <Space>
            <Switch size="small" checked={enabled} onChange={setEnabled} disabled={!editable} />
            <Text style={labelStyle}>{L.enabled}</Text>
          </Space>
          {!enabled && (
            <div>
              <Text style={{ ...hintStyle, color: '#faad14' }}>{L.enabledOffHint}</Text>
            </div>
          )}
        </div>

        <Row gutter={[16, 12]}>
          <Col xs={24} md={12}>
            <Text style={labelStyle}>{L.webUsers}</Text>
            <Select
              mode="tags"
              open={false}
              value={webUsers}
              onChange={(v: string[]) => setWebUsers(v)}
              tokenSeparators={[',', ' ', '，']}
              disabled={!editable}
              style={{ width: '100%', marginTop: '0.25rem' }}
            />
            <Text style={hintStyle}>{L.webUsersHint}</Text>
            {enabled && webUsers.length === 0 && (
              <div><Text style={{ ...hintStyle, color: '#faad14' }}>{L.emptyWarn}</Text></div>
            )}
          </Col>
          <Col xs={24} md={12}>
            <Text style={labelStyle}>{L.waNumbers}</Text>
            <Select
              mode="tags"
              open={false}
              value={waNumbers}
              onChange={(v: string[]) => setWaNumbers(v)}
              tokenSeparators={[',', '，', ';']}
              disabled={!editable}
              style={{ width: '100%', marginTop: '0.25rem' }}
            />
            <Text style={hintStyle}>{L.waNumbersHint}</Text>
            {enabled && waNumbers.length === 0 && (
              <div><Text style={{ ...hintStyle, color: '#faad14' }}>{L.emptyWarn}</Text></div>
            )}
          </Col>
          <Col xs={24} md={12}>
            <Text style={labelStyle}>{L.denyZh}</Text>
            <TextArea
              value={denyZh}
              onChange={(e) => setDenyZh(e.target.value)}
              autoSize={{ minRows: 2, maxRows: 4 }}
              disabled={!editable}
              style={{ marginTop: '0.25rem', ...inputStyle }}
            />
          </Col>
          <Col xs={24} md={12}>
            <Text style={labelStyle}>{L.denyEn}</Text>
            <TextArea
              value={denyEn}
              onChange={(e) => setDenyEn(e.target.value)}
              autoSize={{ minRows: 2, maxRows: 4 }}
              disabled={!editable}
              style={{ marginTop: '0.25rem', ...inputStyle }}
            />
          </Col>
        </Row>

        <div style={{ marginTop: '1rem' }}>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!editable} onClick={save}>
            {L.save}
          </Button>
        </div>
      </Card>
    </div>
  )
}
