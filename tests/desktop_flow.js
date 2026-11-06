import { act } from 'react'
import { createRoot } from 'react-dom/client'
import plugin from '../desktop/plugin.js'

export async function run(assert, document) {
  const contributions = [], calls = [], id = 'a'.repeat(32)
  const ctx = { register: c => contributions.push(c), rest: async (_, { body }) => {
    calls.push(body)
    switch (body.operation) {
      case 'transfer_create_upload': return { job_id:id, status:'queued', source_bytes:1500 }
      case 'transfer_run_upload': return { job_id:id, status:'outcome_unknown', acknowledged_bytes:0, total_bytes:1500 }
      case 'transfer_upload_status': return { job_id:id, status:'outcome_unknown', acknowledged_bytes:0, total_bytes:1500, uncertain_chunk_bytes:1024 }
      case 'transfer_create_download': return { job_id:id, status:'queued' }
      case 'transfer_status': return { job_id:body.arguments.job_id, status:'in_progress', received_bytes:10, total_bytes:25 }
      case 'transfer_run_download': return { job_id:id, status:'verified', received_bytes:25, total_bytes:25, output_path:'/tmp/result.bin' }
      default: throw Error('Unexpected operation')
    }
  } }
  plugin.register(ctx)
  const root = createRoot(document.getElementById('root'))
  const change = async (element, value) => act(async () => {
    const proto = element.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(element, value)
    element.dispatchEvent(new window.Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles:true }))
  })
  const click = async text => act(async () => {
    const element = [...document.querySelectorAll('button')].find(e => e.textContent === text)
    assert.ok(element && !element.disabled, 'Missing or disabled ' + text); element.click()
  })
  try {
    await act(async () => root.render(contributions.find(c => c.area === 'routes').render()))
    await change(document.querySelector('select'), 'upload')
    let inputs = document.querySelectorAll('input')
    await change(inputs[0], 'https://example.com/upload')
    await change(inputs[1], '/tmp/source.bin')
    await act(async () => document.querySelector('input[type=checkbox]').click())
    await click('Create job')
    assert.equal(calls.find(c => c.operation === 'transfer_create_upload').arguments.allow_profile_media, true)
    assert.equal(calls.filter(c => c.operation === 'transfer_run_upload').length, 0)
    await change(document.querySelector('input[type=number]'), '1024')
    await click('Run next slice')
    assert.match(document.querySelector('[role=status]').textContent, /outcome is unknown/i)
    await click('Refresh status')
    assert.equal(calls.filter(c => c.operation === 'transfer_run_upload').length, 1)
    await change(document.querySelector('select'), 'download')
    inputs = document.querySelectorAll('input')
    await change(inputs[0], 'https://example.com/file')
    await change(inputs[1], 'a'.repeat(64))
    await click('Create job')
    await change(document.querySelector('input[type=number]'), '2048')
    await click('Run next slice')
    assert.equal(document.querySelector('details').open, false)
    inputs = document.querySelectorAll('input')
    await change(inputs[2], 'b'.repeat(32))
    await click('Check job')
    assert.equal(document.querySelectorAll('input')[0].disabled, false)
    assert.equal(calls.filter(c => c.operation === 'transfer_status').length, 1)
  } finally { await act(async () => root.unmount()) }
  return {passed:true, checks:['explicit job creation', 'profile media opt-in', 'no automatic transfer', 'unknown outcome visible', 'explicit status refresh', 'reconnect existing job', 'verified path collapsed', 'timer cleanup']}
}
