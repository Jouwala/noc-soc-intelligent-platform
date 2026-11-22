/**
 * SiteSyncOps —— 数据同步操作台（分组式，eflow 为第一组，后续网元照此扩展）。
 *
 * 与 Email Reports →「站点同步」页签的分工：
 * - 那边是老的「生成差异文件」入口（保留不动）；
 * - 本页是完整操作流水线：生成文件 → 预览写库（dry-run）→ 二次确认写库。
 * 写库走 /api/cmdb-site-update/writeback/*，令牌绑定源文件指纹，防误点、防源表被换。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Button, Card, Col, Descriptions, Modal, Row, Select, Space, Spin,
  Table, Tag, Typography, message,
} from 'antd'
import {
  CheckCircleOutlined, CloudUploadOutlined, DownloadOutlined, ExclamationCircleOutlined,
  EyeOutlined, FileExcelOutlined, ReloadOutlined, SafetyCertificateOutlined,
} from '@ant-design/icons'
import api from '../api'
import { useI18n } from '../i18n'
import { cardStyle, pageWrap } from '../theme'

const { Title, Paragraph, Text } = Typography

type PgPreview = { update: number; insert: number; before: number; after: number; pruned: number }
type Preview = {
  ok: boolean; error?: string; pg?: PgPreview; token?: string;
  source_file?: string; source_mtime?: string; source_path?: string; note?: string;
}
type WriteResult = {
  ok: boolean; error?: string; pg?: PgPreview; backup?: { path: string; rows: number };
  source_file?: string; note?: string;
}
type DiffCounts = { A: number; M: number; D: number; unchanged: number }
type OutFile = { name: string; rel_path: string; size: number; mtime: string }
type GenSummary = {
  success: boolean; error?: string; source_file?: string; elapsed_seconds?: number;
  finished_at?: string;
  eflow?: DiffCounts; master?: DiffCounts; degraded?: boolean;
  files?: OutFile[];
}
type GenStatus = { work_dir: string; out_dir: string; last_run: GenSummary | null; files: OutFile[] }
type SourceFile = { name: string; size: number; mtime: string; is_latest: boolean }

const countTag = (label: string, value: number | undefined, color: string) => (
  <Tag color={color} style={{ margin: 0 }}>{label}: {value ?? '—'}</Tag>
)

export default function SiteSyncOps() {
  const { t } = useI18n()
  const [status, setStatus] = useState<GenStatus | null>(null)
  const [genLoading, setGenLoading] = useState(false)
  const [genRunning, setGenRunning] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [writeLoading, setWriteLoading] = useState(false)
  const [writeResult, setWriteResult] = useState<WriteResult | null>(null)
  const [lastWrite, setLastWrite] = useState<WriteResult | null>(null)
  const [sources, setSources] = useState<SourceFile[]>([])
  const [selectedSource, setSelectedSource] = useState<string | undefined>(undefined)

  const load = useCallback(async () => {
    setGenLoading(true)
    try {
      const [gen, wb, src] = await Promise.all([
        api.get('/cmdb-site-update/summary'),
        api.get('/cmdb-site-update/writeback/last'),
        api.get('/cmdb-site-update/sources'),
      ])
      setStatus(gen.data || null)
      setLastWrite(wb.data?.last_run || null)
      const list: SourceFile[] = src.data?.sources || []
      setSources(list)
      // 首次加载默认选最新；之后保留用户已选（若仍在列表里）
      setSelectedSource(prev => {
        if (prev && list.some(s => s.name === prev)) return prev
        return list.find(s => s.is_latest)?.name || list[0]?.name
      })
    } catch {
      message.error(t('siteOps.loadFail'))
    } finally {
      setGenLoading(false)
    }
  }, [t])

  useEffect(() => { void load() }, [load])

  const generate = useCallback(async () => {
    if (!selectedSource) {
      message.warning(t('siteOps.sourceRequired'))
      return
    }
    setGenRunning(true)
    try {
      // 专用同步接口：可指定源文件，跑完直接拿本次 A/M/D 摘要
      const res = await api.post('/cmdb-site-update/generate', { source: selectedSource })
      if (!res.data?.success) {
        message.error(`${t('siteOps.genFail')}: ${res.data?.error || ''}`)
      } else {
        message.success(t('siteOps.genDone'))
        setPreview(null)  // 源表在生成时可能被刷新，旧令牌作废
      }
      void load()
    } catch {
      message.error(t('siteOps.genFail'))
    } finally {
      setGenRunning(false)
    }
  }, [load, selectedSource, t])

  const changeSource = useCallback((name: string) => {
    setSelectedSource(name)
    setPreview(null)  // 换源表必须重新预览，令牌绑定的是旧源表
  }, [])

  const doPreview = useCallback(async () => {
    if (!selectedSource) {
      message.warning(t('siteOps.sourceRequired'))
      return
    }
    setPreviewLoading(true)
    setPreview(null)
    try {
      const res = await api.get('/cmdb-site-update/writeback/preview', {
        params: { source: selectedSource },
      })
      setPreview(res.data || null)
      if (!res.data?.ok) message.error(res.data?.error || t('siteOps.previewFail'))
    } catch (e: any) {
      const err = e?.response?.data?.detail || t('siteOps.previewFail')
      setPreview({ ok: false, error: String(err) })
      message.error(String(err))
    } finally {
      setPreviewLoading(false)
    }
  }, [selectedSource, t])

  const doWrite = useCallback(async () => {
    if (!preview?.token) return
    setWriteLoading(true)
    try {
      const res = await api.post('/cmdb-site-update/writeback/apply', {
        token: preview.token, confirm: true, source: selectedSource,
      })
      setWriteResult(res.data || null)
      if (res.data?.ok) {
        message.success(t('siteOps.writeOk'))
        setConfirmOpen(false)
        setPreview(null)
        void load()
      } else {
        message.error(res.data?.error || t('siteOps.writeFail'))
      }
    } catch (e: any) {
      const err = e?.response?.data?.detail || t('siteOps.writeFail')
      setWriteResult({ ok: false, error: String(err) })
      message.error(String(err))
    } finally {
      setWriteLoading(false)
    }
  }, [preview, selectedSource, load, t])

  const summary = status?.last_run
  const files = status?.files || []

  return (
    <div className="wh-page" style={pageWrap}>
      <Title level={4} style={{ marginTop: 0 }}>{t('siteOps.title')}</Title>
      <Paragraph type="secondary">{t('siteOps.hint')}</Paragraph>

      {/* 网元分组：eflow 是第一组，其他网元后续按同样骨架往这里加 */}
      <Card title={<Space><FileExcelOutlined />{t('siteOps.groupEflow')}</Space>} style={cardStyle}>
        <Spin spinning={genLoading}>
          {/* 源表选择：不再写死某个文件名，db 目录下的候选自动列出 */}
          <Alert type="info" showIcon style={{ marginBottom: 16 }}
                 message={<Space wrap>
                   <Text strong>{t('siteOps.sourceSelect')}：</Text>
                   {sources.length ? (
                     <Select
                       size="small"
                       style={{ minWidth: 360, maxWidth: '100%' }}
                       value={selectedSource}
                       onChange={(v) => changeSource(v)}
                       options={sources.map(s => ({
                         value: s.name,
                         label: `${s.name}　(${s.mtime}${s.is_latest ? ` · ${t('siteOps.sourceLatest')}` : ''})`,
                       }))}
                     />
                   ) : <Text type="secondary">{t('siteOps.sourceEmpty')}</Text>}
                 </Space>}
                 description={t('siteOps.sourceSelectHint')} />

          <Row gutter={[16, 16]}>
            <Col xs={24} lg={12}>
              <Card size="small" title={t('siteOps.step1')} extra={
                <Button size="small" icon={<ReloadOutlined />} onClick={() => void load()} />
              }>
                <Space wrap style={{ marginBottom: 12 }}>
                  {countTag('eflow A', summary?.eflow?.A, 'green')}
                  {countTag('eflow M', summary?.eflow?.M, 'orange')}
                  {countTag('master A', summary?.master?.A, 'green')}
                  {countTag('master M', summary?.master?.M, 'orange')}
                  {countTag('master D', summary?.master?.D, 'red')}
                </Space>
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label={t('siteOps.sourceFile')}>
                    {summary?.source_file || '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label={t('siteOps.lastGen')}>
                    {summary?.finished_at || t('emailReports.siteUpdateNeverRun')}
                  </Descriptions.Item>
                  <Descriptions.Item label={t('siteOps.elapsed')}>
                    {summary?.elapsed_seconds != null ? `${summary.elapsed_seconds}s` : '—'}
                  </Descriptions.Item>
                </Descriptions>
                <Button type="primary" icon={<FileExcelOutlined />} loading={genRunning}
                        onClick={() => void generate()} style={{ marginTop: 12 }} block>
                  {genRunning ? t('siteOps.genRunning') : t('siteOps.genBtn')}
                </Button>
                <Paragraph type="secondary" style={{ fontSize: 12, margin: '8px 0 0' }}>
                  {t('siteOps.genNote')}
                </Paragraph>
              </Card>
            </Col>

            <Col xs={24} lg={12}>
              <Card size="small" title={t('siteOps.step2')}>
                <Paragraph type="secondary" style={{ fontSize: 12 }}>
                  {t('siteOps.previewHint')}
                </Paragraph>
                {preview && !preview.ok && (
                  <Alert type="error" showIcon message={preview.error || t('siteOps.previewFail')} />
                )}
                {preview?.ok && (
                  <>
                    <Descriptions size="small" column={2} bordered>
                      <Descriptions.Item label={t('siteOps.sourceFile')} span={2}>
                        {preview.source_file || '—'}
                      </Descriptions.Item>
                      <Descriptions.Item label={t('siteOps.updateRows')}>
                        <Text strong style={{ color: '#d48806' }}>{preview.pg?.update ?? '—'}</Text>
                      </Descriptions.Item>
                      <Descriptions.Item label={t('siteOps.insertRows')}>
                        <Text strong style={{ color: '#389e0d' }}>{preview.pg?.insert ?? '—'}</Text>
                      </Descriptions.Item>
                      <Descriptions.Item label={t('siteOps.beforeRows')}>{preview.pg?.before ?? '—'}</Descriptions.Item>
                      <Descriptions.Item label={t('siteOps.afterRows')}>{preview.pg?.after ?? '—'}</Descriptions.Item>
                    </Descriptions>
                    <Space style={{ marginTop: 12 }} wrap>
                      <Button icon={<EyeOutlined />} loading={previewLoading}
                              onClick={() => void doPreview()}>{t('siteOps.rePreview')}</Button>
                      <Button type="primary" danger icon={<CloudUploadOutlined />}
                              disabled={!preview.token}
                              onClick={() => setConfirmOpen(true)}>{t('siteOps.writeBtn')}</Button>
                    </Space>
                  </>
                )}
                {!preview && (
                  <Button icon={<EyeOutlined />} loading={previewLoading} block
                          onClick={() => void doPreview()}>{t('siteOps.previewBtn')}</Button>
                )}
                <Alert type="warning" showIcon style={{ marginTop: 12 }}
                       message={t('siteOps.writeWarn')} />
              </Card>
            </Col>
          </Row>

          {lastWrite && (
            <Alert style={{ marginTop: 16 }} type={lastWrite.ok ? 'success' : 'error'} showIcon
                   icon={lastWrite.ok ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />}
                   message={lastWrite.ok ? t('siteOps.lastWriteOk') : t('siteOps.lastWriteFail')}
                   description={lastWrite.ok
                     ? `${lastWrite.source_file || ''} · update ${lastWrite.pg?.update ?? '—'} / insert ${lastWrite.pg?.insert ?? '—'} · ${t('siteOps.backupAt')}: ${lastWrite.backup?.path || '—'}`
                     : lastWrite.error} />
          )}

          {writeResult && !writeResult.ok && (
            <Alert style={{ marginTop: 12 }} type="error" showIcon
                   message={t('siteOps.writeFail')} description={writeResult.error} />
          )}

          <Title level={5} style={{ marginTop: 20 }}>{t('siteOps.files')}</Title>
          <Table rowKey="rel_path" size="small" dataSource={files} pagination={false}
                 columns={[
                   { title: t('siteOps.colFile'), dataIndex: 'rel_path',
                     render: (v: string) => (
                       <a href={`/api/cmdb-site-update/download?path=${encodeURIComponent(v)}`} download>
                         <FileExcelOutlined /> {v}
                       </a>
                     ) },
                   { title: t('siteOps.colSize'), dataIndex: 'size', width: 110,
                     render: (v: number) => `${(v / 1024).toFixed(1)} KB` },
                   { title: t('siteOps.colTime'), dataIndex: 'mtime', width: 180 },
                   { title: t('siteOps.colAction'), width: 90,
                     render: (_: unknown, row: OutFile) => (
                       <a href={`/api/cmdb-site-update/download?path=${encodeURIComponent(row.rel_path)}`} download>
                         <DownloadOutlined /> {t('siteOps.download')}
                       </a>
                     ) },
                 ]} />
          {summary?.degraded && (
            <Alert type="warning" showIcon style={{ marginTop: 12 }}
                   message={t('emailReports.siteUpdateDegraded')} />
          )}
        </Spin>
      </Card>

      <Modal open={confirmOpen} onCancel={() => setConfirmOpen(false)}
             title={<Space><SafetyCertificateOutlined />{t('siteOps.confirmTitle')}</Space>}
             okText={t('siteOps.confirmOk')} cancelText={t('siteOps.cancel')}
             okButtonProps={{ danger: true, loading: writeLoading }}
             onOk={() => void doWrite()}>
        <Paragraph>{t('siteOps.confirmBody')}</Paragraph>
        <Descriptions size="small" column={1} bordered>
          <Descriptions.Item label={t('siteOps.sourceFile')}>{preview?.source_file || '—'}</Descriptions.Item>
          <Descriptions.Item label={t('siteOps.updateRows')}>{preview?.pg?.update ?? '—'}</Descriptions.Item>
          <Descriptions.Item label={t('siteOps.insertRows')}>{preview?.pg?.insert ?? '—'}</Descriptions.Item>
          <Descriptions.Item label={t('siteOps.deleteRows')}>{preview?.pg?.pruned ?? 0}（{t('siteOps.noDelete')}）</Descriptions.Item>
        </Descriptions>
        <Alert type="warning" showIcon style={{ marginTop: 12 }} message={t('siteOps.confirmWarn')} />
      </Modal>
    </div>
  )
}
