import { ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { createElement as h, useState } from 'react'

const field = { padding: '8px 10px', border: '1px solid var(--ui-stroke-secondary)', borderRadius: 7, background: 'var(--ui-bg-tertiary)', color: 'var(--ui-text-primary)' }
const card = { padding: 16, border: '1px solid var(--ui-stroke-secondary)', borderRadius: 12, display: 'grid', gap: 10 }
const row = { display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }
const button = (text, onClick, disabled = false, props = {}) => h('button', { style: field, onClick, disabled, ...props }, text)
const label = (text, input) => h('label', null, text, input)

function Transfers({ ctx }) {
  const [mode, setMode] = useState('download'), [url, setUrl] = useState(''), [source, setSource] = useState('')
  const [sha, setSha] = useState(''), [rate, setRate] = useState(''), [jobId, setJobId] = useState(''), [job, setJob] = useState(null)
  const [allowProfileMedia, setAllowProfileMedia] = useState(false)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const call = (operation, args) => ctx.rest('/action', { method: 'POST', body: { operation, arguments: args }, timeoutMs: 90000 })
  const task = async fn => { setBusy(true); setError(''); try { await fn() } catch (e) { setError(String(e.message || e)) } finally { setBusy(false) } }
  const status = async id => { const result = await call(mode === 'upload' ? 'transfer_upload_status' : 'transfer_status', { job_id: id }); setJobId(id); setJob(result) }
  const create = () => task(async () => {
    const args = mode === 'upload' ? { source_path: source, url, allow_profile_media: allowProfileMedia } : { url, sha256: sha.trim() }
    const result = await call(mode === 'upload' ? 'transfer_create_upload' : 'transfer_create_download', args)
    setJobId(result.job_id); setJob(result)
  })
  const numericRate = Number(rate)
  const validRate = Number.isSafeInteger(numericRate) && numericRate >= 1024 && numericRate <= 20971520
  return h('main', { style: { maxWidth: 980, padding: 28, margin: 'auto', display: 'grid', gap: 20, color: 'var(--ui-text-primary)' } },
    h('header', null, h('h1', null, 'Transfer Queue'), h('p', null, 'Control each transfer slice and review its result.')),
    error && h('p', { role: 'alert', style: card }, error),
    h('section', { style: card, 'aria-label': 'Transfer setup' },
      label('Transfer type', h('select', { style: { ...field, display: 'block' }, value: mode, disabled: busy, onChange: e => { setMode(e.target.value); setJob(null); setJobId('') } },
        h('option', { value: 'download' }, 'HTTPS download'), h('option', { value: 'upload' }, 'Existing tus upload'))),
      label('HTTPS URL (no credentials, query string, or redirect)', h('input', { style: { ...field, display: 'block', width: '100%' }, type: 'url', value: url, disabled: busy, onChange: e => setUrl(e.target.value) })),
      mode === 'upload' ? h('div', null,
        label('Local source file on the Hermes host', h('input', { style: { ...field, display: 'block', width: '100%' }, value: source, disabled: busy || !!job, onChange: e => setSource(e.target.value) })),
        label('Allow selected media inside this profile’s plugin data', h('input', { type: 'checkbox', checked: allowProfileMedia, disabled: busy || !!job, onChange: e => setAllowProfileMedia(e.target.checked) })),
        h('p', null, 'The destination must be an already-created tus resource with the matching length and zero offset. Running a chunk sends the file to that server. Remote content is not checksum-verified.'))
      : label('Expected SHA-256', h('input', { style: { ...field, display: 'block', width: '100%' }, value: sha, disabled: busy || !!job, onChange: e => setSha(e.target.value) })),
      h('div', { style: row }, button('Create job', create, busy || !!job || !url.trim() || (mode === 'upload' ? !source.trim() : !/^[0-9a-f]{64}$/.test(sha.trim())))),
      h('p', null, 'Jobs do not start automatically. Only use a destination you trust.')),
    h('section', { style: card, 'aria-label': 'Transfer control' },
      label('Existing job ID', h('input', { style: { ...field, display: 'block', width: '100%' }, value: jobId, disabled: busy, onChange: e => { setJobId(e.target.value); setJob(null) } })),
      h('div', { style: row }, button('Check job', () => task(() => status(jobId.trim())), busy || !jobId.trim())),
      label('Maximum bytes per second', h('input', { style: { ...field, display: 'block' }, type: 'number', min: 1024, max: 20971520, step: 1, value: rate, disabled: busy, onChange: e => setRate(e.target.value) })),
      job && h('div', { role: 'status' }, h('strong', null, `Status: ${job.status}`),
        h('p', null, `${job.received_bytes ?? job.acknowledged_bytes ?? 0} of ${job.total_bytes ?? job.source_bytes ?? 'unknown'} bytes`),
        job.status === 'outcome_unknown' && h('p', null, 'The previous upload outcome is unknown. The next explicit run reconciles the server offset without resending.'),
        job.notice && h('p', null, job.notice),
        job.output_path && h('details', null, h('summary', null, 'Show verified file location'), h('code', { style: { overflowWrap: 'anywhere' } }, job.output_path))),
      h('div', { style: row }, button('Run next slice', () => task(async () => {
        const args = { job_id: jobId.trim(), url, bytes_per_second: numericRate }
        if (mode === 'download') args.max_seconds = 15
        setJob(await call(mode === 'upload' ? 'transfer_run_upload' : 'transfer_run_download', args))
      }), busy || !job || !url.trim() || !validRate || ['verified', 'accepted', 'checksum_mismatch'].includes(job.status)),
      button('Refresh status', () => task(() => status(jobId.trim())), busy || !jobId.trim()))))
}

export default { id: 'transfer-queue', name: 'Transfer Queue', register(ctx) {
  ctx.register({ id: 'page', area: ROUTES_AREA, data: { path: '/transfer-queue' }, render: () => h(Transfers, { ctx }) })
  ctx.register({ id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: '/transfer-queue', label: 'Transfers', codicon: 'cloud-upload' } })
} }
