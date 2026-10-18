// 브라우저 E2E: 원본 스크립트·받아 적기 작업이 회의를 방해하지 않는지
//   (1) 올린 파일이 구간마다 기록에 바로 보임 (2) 다른 회의를 녹음하면 작업이 멈췄다가 이어짐
//   (3) 배터리면 녹음한 회의의 원본 스크립트를 기다리고, ⋯ → 지금 만들기는 바로 만듦
// 사용법: 아래처럼 서버를 띄운 뒤 (빈 데이터 폴더, 시연용 엔진)
//   SCRIBE_WINDOW_S=20 SCRIBE_MOCK_FINAL_DELAY_S=2 SCRIBE_POWER=battery SCRIBE_PARALLEL_DIARIZE_MIN_S=100000 (+ 시연용 엔진 설정) uvicorn …
//   OUT=scripts/e2e/out FAKE_AUDIO=scripts/e2e/out/speech.wav node scripts/e2e/jobs.cjs
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright')
const fs = require('fs')
const OUT = process.env.OUT || 'scripts/e2e/out'
const SPEECH = process.env.FAKE_AUDIO || `${OUT}/speech.wav`
const BASE = process.env.BASE || 'http://127.0.0.1:8765'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** 가짜 마이크 소리를 이어 붙여 95초 넘는 '올릴 파일'을 만든다 (16-bit WAV, 44바이트 머리) */
function longWav(path, seconds) {
  const wav = fs.readFileSync(path)
  const rate = wav.readUInt32LE(24)
  const data = wav.subarray(44)
  const reps = Math.ceil((seconds * rate * 2) / data.length)
  const body = Buffer.concat(Array(reps).fill(data))
  const head = Buffer.from(wav.subarray(0, 44))
  head.writeUInt32LE(36 + body.length, 4)
  head.writeUInt32LE(body.length, 40)
  return Buffer.concat([head, body])
}
let fails = 0
const check = (ok, label) => { console.log(`${ok ? '✓' : '✗ FAILED'} ${label}`); if (!ok) fails++ }
const api = async (path, opts) => (await fetch(BASE + path, opts)).json()
;(async () => {
  const b = await chromium.launch({
    executablePath: process.env.CHROME || undefined,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${SPEECH}`],
  })
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['microphone'] })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

  // (1) 올린 파일: 구간마다 기록에 바로
  const fd = new FormData()
  fd.append('file', new Blob([longWav(SPEECH, 95)], { type: 'audio/wav' }), '긴 회의.wav')
  fd.append('title', '')
  const up = await api('/api/upload', { method: 'POST', body: fd })
  await page.goto(`${BASE}/#/m/${up.id}`)
  await page.waitForSelector('.transcript .utext', { timeout: 30000 })
  const firstCount = await page.locator('.transcript .utext').count()
  const jobAtFirst = (await api(`/api/meetings/${up.id}/job`)).job
  check(jobAtFirst.status === 'running', `받아 적는 중에 기록이 먼저 보임 (문장 ${firstCount}개, 작업 ${jobAtFirst.status}, ${Math.round(jobAtFirst.progress * 100)}%)`)
  const banner = await page.locator('.proc-banner').innerText().catch(() => '')
  check(/받아 적는 중/.test(banner) && /다 받아 적은 부분부터/.test(banner), `작은 띠: "${banner.replace(/\s+/g, ' ').trim()}"`)
  await page.screenshot({ path: `${OUT}/jobs-1-upload-progressive.png` })

  // (2) 다른 회의 녹음 → 받아 적기가 멈춤
  const page2 = await ctx.newPage()
  await page2.goto(BASE)
  await page2.waitForSelector('.welcome h1')
  await page2.click('.start-card.primary')
  await page2.waitForSelector('.dock-rec', { timeout: 15000 })
  let waiting = null
  for (let i = 0; i < 40 && !waiting; i++) {
    const j = (await api(`/api/meetings/${up.id}/job`)).job
    if (j.status !== 'running') break
    waiting = j.waiting
    await sleep(250)
  }
  check(!!waiting && waiting.startsWith('녹음 중이라 잠시 멈췄어요'), `녹음하는 동안 멈춤: "${waiting}"`)
  const p1 = (await api(`/api/meetings/${up.id}/job`)).job.progress
  await sleep(3000)
  const p2 = (await api(`/api/meetings/${up.id}/job`)).job.progress
  check(p1 === p2, `멈춘 동안 진행률 그대로 (${Math.round(p1 * 100)}% → ${Math.round(p2 * 100)}%)`)
  await page.bringToFront()
  await page.waitForSelector('.proc-banner.waiting', { timeout: 5000 }).catch(() => null)
  const wb = await page.locator('.proc-banner').innerText().catch(() => '')
  check(/녹음 중이라 잠시 멈췄어요/.test(wb), `올린 회의 화면의 띠: "${wb.replace(/\s+/g, ' ').trim()}"`)
  await page.screenshot({ path: `${OUT}/jobs-2-paused-while-recording.png` })

  // 녹음 마침 (배터리) → 받아 적기는 이어서 끝나고, 녹음한 회의의 원본 스크립트는 전원을 기다린다
  await page2.bringToFront()
  await sleep(2500)
  await page2.click('.btn-stop')
  await page2.click('.modal .btn-danger')
  await page2.waitForSelector('.dock-rec', { state: 'detached', timeout: 20000 })
  const recId = (await api('/api/meetings')).find((m) => m.source === 'live').id
  let done = null
  for (let i = 0; i < 120 && !done; i++) {
    const j = (await api(`/api/meetings/${up.id}/job`)).job
    if (j.status === 'done' || j.status === 'error') done = j
    await sleep(500)
  }
  check(done?.status === 'done', `녹음을 마치자 받아 적기가 이어서 끝남 (${done?.status})`)
  await page.bringToFront()
  await page.waitForSelector('.transcript .spk-chip', { timeout: 10000 }).catch(() => null)
  const finalCount = await page.locator('.transcript .utext').count()
  const live = await api(`/api/meetings/${up.id}/live`)
  check(live.units.length > firstCount && live.units.every((u) => u.speaker_auto), `다 받아 적은 뒤 문장 ${live.units.length}개(처음 ${firstCount}개), 모두 화자 묶음`)
  check((await page.locator('.transcript .spk-chip').count()) > 0, `화자 칩이 생김 (화면 문장 ${finalCount}개)`)
  await page.screenshot({ path: `${OUT}/jobs-3-upload-done.png` })

  // (3) 배터리: 녹음한 회의의 원본 스크립트는 기다린다
  await page2.bringToFront()
  await page2.waitForSelector('.proc-banner.waiting', { timeout: 10000 }).catch(() => null)
  const pb = await page2.locator('.proc-banner').innerText().catch(() => '')
  check(/전원을 연결하면 만들어요/.test(pb), `배터리면 기다림: "${pb.replace(/\s+/g, ' ').trim()}"`)
  const pill = await page2.locator('.status-pill').innerText()
  check(pill.includes('녹음 완료'), `기다리는 동안 상태는 "${pill}"`)
  await page2.screenshot({ path: `${OUT}/jobs-4-waiting-for-power.png` })
  await page2.click('.page-header .icon-btn[title="더 보기"]')
  const item = page2.locator('.menu-item', { hasText: '원본 스크립트 지금 만들기' })
  check(await item.count() === 1, '⋯ 메뉴에 "원본 스크립트 지금 만들기"')
  await item.click()
  let rj = null
  for (let i = 0; i < 120; i++) {
    rj = (await api(`/api/meetings/${recId}/job`)).job
    if (rj.status === 'done' || rj.status === 'error') break
    await sleep(500)
  }
  check(rj?.status === 'done' && !rj.auto, `지금 만들기 → 배터리여도 만듦 (${rj?.status})`)
  await page2.click('.page-header .icon-btn[title="더 보기"]')
  check(await page2.locator('.menu-item', { hasText: '원본 스크립트 받기' }).count() === 1, '이제 원본 스크립트 받기가 보임')
  check(errors.length === 0, `콘솔 오류 없음 ${JSON.stringify(errors)}`)
  await b.close()
  console.log(fails ? `FAILED ${fails}` : 'ALL OK')
  process.exit(fails ? 1 : 0)
})()
