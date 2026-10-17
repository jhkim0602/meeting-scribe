// 브라우저 E2E: 회의 중 예외 처리
//  - 일시정지 중 말소리 알림 → 이어하기 → 쉬는 시간 구분선
//  - 직접 구분선 넣기
//  - 마이크 끊김 → 자동 재연결
//  - 서버 재시작 → 소리를 모아 두었다가 이어 보내기 (구분선 없음)
//  - 종료 확인창 → 계속 녹음 / 종료 → 이어서 녹음(원본 작업 취소)
//  - 원본에서 쉬는 시간 경계로 문장이 나뉨
//  - 조용한 마이크: 쉬는 시간인가요? / 완전 무음: 마이크 경고
// 서버는 SCRIBE_MIN_BREAK_S=2, 시연용 엔진으로 띄운다. RESTART_CMD는 서버를 다시 띄우는 셸 명령.
const { execSync } = require('child_process')
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright')
const OUT = process.env.OUT || 'scripts/e2e/out'
const BASE = process.env.BASE || 'http://127.0.0.1:8765'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const get = async (p) => (await fetch(BASE + p)).json()

function check(cond, msg) {
  if (!cond) throw new Error('확인 실패: ' + msg)
  console.log('✓', msg)
}

async function launch(audio) {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME || undefined,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${audio}`],
  })
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['microphone'] })).newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => { if (m.type() === 'error' && !/WebSocket|ERR_CONNECTION_REFUSED|Failed to load resource/.test(m.text())) errors.push(m.text()) })
  return { browser, page, errors }
}

async function main() {
  // ---------- A. 말소리가 나오는 마이크 ----------
  const { browser, page, errors } = await launch(`${OUT}/speech.wav`)
  await page.goto(BASE)
  await page.click('.start-card.primary')
  await page.waitForSelector('.dock-rec', { timeout: 15000 })
  const mid = (await get('/api/meetings'))[0].id
  await sleep(6000)

  // 1) 일시정지 → 말소리 감지 알림
  await page.click('.btn-round[title^="일시정지"]')
  await page.waitForSelector('.chip-paused')
  await page.waitForSelector('.rec-alert:has-text("녹음이 일시정지된 상태예요")', { timeout: 12000 })
  check(true, '일시정지 중 말소리 → "녹음이 일시정지된 상태예요" 알림')
  await page.screenshot({ path: `${OUT}/b1-paused-speech.png` })
  await page.click('.rec-alert button:has-text("녹음 이어하기")')
  await sleep(1200)
  check((await page.locator('.view-live .divider-row').count()) === 1, '이어하기 → 쉬는 시간 구분선 생김')
  const dvText = await page.locator('.view-live .divider-row').first().innerText()
  check(/쉬는 시간/.test(dvText) && /\d\d:\d\d–\d\d:\d\d/.test(dvText) && !/[\u{1F300}-\u{1FAFF}]/u.test(dvText), `구분선 표시: "${dvText.replace(/\n/g, ' ')}" (이모지 없음)`)

  // 2) 직접 구분선 넣기
  await sleep(3000)
  const unit = page.locator('.view-live .unit:not(.partial)').last()
  await unit.hover()
  await unit.locator('.u-act[title^="이 문장 뒤에 구분선"]').click()
  await page.fill('.composer textarea', '2부')
  await page.keyboard.press('Enter')
  await sleep(700)
  check((await page.locator('.view-live .divider-row:has-text("2부")').count()) === 1, '직접 구분선 "2부" 넣기')
  await page.screenshot({ path: `${OUT}/b2-dividers.png` })

  // 3) 마이크 끊김 → 자동 재연결
  await page.evaluate(() => window.__scribeDebug.endTrack())
  await page.waitForSelector('.toast:has-text("마이크를 다시 연결했어요")', { timeout: 10000 })
  const t1 = await page.locator('.rec-time').innerText()
  await sleep(2500)
  const t2 = await page.locator('.rec-time').innerText()
  check(t1 !== t2 && (await page.locator('.rec-alert').count()) === 0, `마이크 끊김 → 다시 연결, 녹음 계속 (${t1} → ${t2})`)

  // 4) 서버 재시작 → 모아 둔 소리 이어 보내기
  const before = (await get(`/api/meetings/${mid}/live`)).annotations.filter((a) => a.kind === 'divider').length
  execSync(process.env.RESTART_CMD, { stdio: 'inherit' })
  await page.waitForSelector('.rec-alert:has-text("서버 연결이 끊겼어요")', { timeout: 10000 })
  await page.screenshot({ path: `${OUT}/b3-server-lost.png` })
  await page.waitForSelector('.toast:has-text("서버에 다시 연결했어요")', { timeout: 30000 })
  await sleep(1500)
  const st = await page.evaluate(() => window.__scribeDebug.state())
  const after = (await get(`/api/meetings/${mid}/live`)).annotations.filter((a) => a.kind === 'divider').length
  check(st.buffered === 0 && after === before, `서버 재시작 → 다시 연결, 모아 둔 소리 전송, 구분선 추가 없음 (${before}→${after})`)
  // 끊긴 동안의 소리까지 서버 파일에 들어갔는지: 브라우저 녹음 시간 ≈ 서버 WAV 길이
  const elapsed = await page.evaluate(() => { const s = window.__scribeDebug.state(); return s.offset + s.sent / 16000 })
  const range = (await fetch(`${BASE}/api/meetings/${mid}/audio`, { headers: { Range: 'bytes=0-0' } })).headers.get('content-range')
  const fileSec = (Number(range.split('/')[1]) - 44) / 32000
  check(Math.abs(elapsed - fileSec) < 1.5, `끊긴 동안 소리도 저장됨: 브라우저 ${elapsed.toFixed(1)}초 ≈ 서버 파일 ${fileSec.toFixed(1)}초`)

  // 5) 종료 확인창
  await page.click('.btn-stop')
  await page.waitForSelector('.modal.confirm')
  await page.screenshot({ path: `${OUT}/b4-confirm.png` })
  await page.click('.modal.confirm button:has-text("계속 녹음")')
  check((await page.locator('.dock-rec').count()) === 1, '확인창에서 "계속 녹음" → 녹음 유지')
  await page.click('.btn-stop')
  await page.click('.modal.confirm button:has-text("녹음 종료")')
  await page.waitForSelector('.toast .toast-action:has-text("이어서 녹음")', { timeout: 20000 })
  check(true, '녹음 종료 → "이어서 녹음" 되돌리기 알림')
  await page.click('.toast .toast-action:has-text("이어서 녹음")')
  await page.waitForSelector('.dock-rec', { timeout: 15000 })
  await sleep(1000)
  const job = (await get(`/api/meetings/${mid}/job`)).job
  check(job.status === 'cancelled' || job.status === 'done', `실수 종료 → 바로 이어서 녹음 (원본 작업: ${job.status})`)
  await sleep(4000)

  // 6) 진짜 종료 → 원본에서 구분선 확인
  await page.click('.btn-stop')
  await page.click('.modal.confirm button:has-text("녹음 종료")')
  await page.waitForSelector('.dock-player', { timeout: 30000 })
  await page.click('.tab:has-text("원본")')
  await page.waitForSelector('.view-final .unit', { timeout: 60000 })
  await sleep(800)
  const fdiv = await page.locator('.view-final .divider-row').count()
  check(fdiv >= 2, `원본에도 구분선 ${fdiv}개 표시`)
  await page.screenshot({ path: `${OUT}/b5-final-dividers.png` })
  const md = await (await fetch(`${BASE}/api/meetings/${mid}/export.md`)).text()
  check(/──── 쉬는 시간 · \d\d:\d\d–\d\d:\d\d · \d+분 ────/.test(md) && md.includes('──── 2부 ────'), '내보내기(AI용)에도 구분선')
  check(errors.length === 0, `콘솔 오류 없음 ${JSON.stringify(errors)}`)
  await browser.close()

  // ---------- B. 조용한 회의실: 쉬는 시간인가요? ----------
  await fetch(`${BASE}/api/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ break_prompt_minutes: 0.1 }) })
  const q = await launch(`${OUT}/quiet.wav`)
  await q.page.goto(BASE)
  await q.page.click('.start-card.primary')
  await q.page.waitForSelector('.dock-rec', { timeout: 15000 })
  const qid = (await get('/api/meetings'))[0].id
  await q.page.waitForSelector('.rec-alert:has-text("쉬는 시간인가요?")', { timeout: 15000 })
  await q.page.screenshot({ path: `${OUT}/b6-silence.png` })
  check((await q.page.locator('.rec-alert:has-text("마이크에서 소리가")').count()) === 0, '조용한 회의실(작은 잡음)은 마이크 경고가 아님')
  await q.page.click('.rec-alert button:has-text("네, 일시정지")')
  await q.page.waitForSelector('.chip-paused')
  const chip = await q.page.locator('.chip-paused').innerText()
  check(/\d+초 지남/.test(chip), `긴 무음 → 일시정지, 조용해진 시점부터 계산 (${chip})`)
  await sleep(1000)
  await q.page.click('.btn-resume')
  await sleep(1500)
  const qd = (await get(`/api/meetings/${qid}/live`)).annotations.filter((a) => a.kind === 'divider')
  check(qd.length === 1 && qd[0].anchor.t < 2, `조용해진 시점(${qd[0]?.anchor.t}s)에 쉬는 시간 구분선`)
  await q.browser.close()
  await fetch(`${BASE}/api/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ break_prompt_minutes: 3 }) })

  // ---------- C. 완전 무음: 마이크 경고 ----------
  const z = await launch(`${OUT}/zero.wav`)
  await z.page.goto(BASE)
  await z.page.click('.start-card.primary')
  await z.page.waitForSelector('.dock-rec', { timeout: 15000 })
  await z.page.waitForSelector('.rec-alert:has-text("마이크에서 소리가 들어오지 않아요")', { timeout: 12000 })
  await z.page.screenshot({ path: `${OUT}/b7-mic-silent.png` })
  check(true, '소리가 완전히 0 → "마이크에서 소리가 들어오지 않아요" 경고')
  await z.browser.close()
}

main().catch((e) => {
  console.error('E2E FAILED', e)
  process.exit(1)
})
