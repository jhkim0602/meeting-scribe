// 브라우저 E2E: 노션처럼 미니멀한 화면
//  - 녹음 표시줄(알약)을 누르면 마이크 고르기·입력 확인·연결 상태
//  - 단축키: ? 목록, M 메모, C 댓글, ⌘⇧P 일시정지/이어하기
//  - 지우기는 확인창 대신 되돌리기 (댓글, 회의)
//  - ⋯ 보기: 댓글 접기, 글자 크기, 실제 시각
//  - 따라가기 버튼 대신 "새 문장 n개" / "재생 위치로"
//  - 설정: 저장 공간, 고급 접기
// 서버는 시연용 엔진으로 띄운다 (run.cjs와 같음).
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright')
const OUT = process.env.OUT || 'scripts/e2e/out'
const FAKE_AUDIO = process.env.FAKE_AUDIO || `${OUT}/speech.wav`
const BASE = process.env.BASE || 'http://127.0.0.1:8765'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const get = async (p) => (await fetch(BASE + p)).json()

function check(cond, msg) {
  if (!cond) throw new Error('확인 실패: ' + msg)
  console.log('✓', msg)
}

;(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME || undefined,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${FAKE_AUDIO}`, '--autoplay-policy=no-user-gesture-required'],
  })
  // 화면을 낮게 해서 짧은 녹음도 스크롤이 생기게 한다
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 640 }, permissions: ['microphone'], colorScheme: 'light' })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + e.message))
  page.on('console', (m) => { if (m.type() === 'error' && !/WebSocket|Failed to load resource/.test(m.text())) errors.push(m.text()) })

  await page.goto(BASE)
  await page.waitForSelector('.welcome h1')
  await page.click('.start-card.primary')
  await page.waitForSelector('.dock-rec', { timeout: 15000 })
  const mid = (await get('/api/meetings'))[0].id
  await sleep(6000)

  // 1) 녹음 표시줄 → 마이크·연결 상태
  await page.click('.rec-pill')
  await page.waitForSelector('.mic-panel')
  const panel = await page.locator('.mic-panel').innerText()
  check(/마이크/.test(panel) && /서버 연결/.test(panel) && /저장 공간/.test(panel) && (await page.locator('.mic-panel .meter').count()) === 1, '녹음 표시줄을 누르면 마이크 고르기·입력 막대·연결 상태')
  await page.screenshot({ path: `${OUT}/u1-mic-panel.png` })
  await page.keyboard.press('Escape')
  check((await page.locator('.mic-panel').count()) === 0, 'Esc로 닫힘')

  // 2) 단축키
  await page.mouse.click(1000, 160)
  await page.keyboard.press('?')
  await page.waitForSelector('.modal.shortcuts')
  await page.screenshot({ path: `${OUT}/u2-shortcuts.png` })
  await page.keyboard.press('Escape')
  check((await page.locator('.modal.shortcuts').count()) === 0, '? → 단축키 목록, Esc로 닫기')
  await page.keyboard.press('m')
  check((await page.locator('.memo-bar.mode-memo').count()) === 1 && (await page.evaluate(() => document.activeElement?.closest('.memo-bar') !== null)), 'M → 아래 입력창이 메모 모드로 열림')
  await page.keyboard.type('단축키로 남긴 메모')
  await page.keyboard.press('Enter')
  await sleep(600)
  check((await page.locator('.view-live .memo-card').count()) === 1, '메모 저장')
  await page.mouse.click(1000, 160)
  await page.keyboard.press('c')
  check((await page.locator('.memo-bar.mode-comment').count()) === 1, 'C (고른 글자 없음) → 방금 문장에 댓글 모드')
  await page.keyboard.type('지울 댓글')
  await page.keyboard.press('Enter')
  await sleep(600)
  await page.mouse.click(1000, 160)
  await page.keyboard.press('Control+Shift+P')
  await page.waitForSelector('.chip-paused', { timeout: 3000 })
  await page.keyboard.press('Control+Shift+P')
  await page.waitForSelector('.btn-round[title^="일시정지"]', { timeout: 3000 })
  check(true, '⌘⇧P → 일시정지, 한 번 더 → 이어서 녹음')

  // 3) 댓글 지우기 → 되돌리기 → 다시 지우면 서버에서도 지워짐
  const cmt = page.locator('.view-live .cmt').first()
  await cmt.hover()
  await cmt.locator('button[title="삭제"]').click()
  await page.waitForSelector('.toast:has-text("댓글을 지웠어요")')
  check((await page.locator('.view-live .cmt').count()) === 0, '지우면 바로 사라지고 "되돌리기" 알림')
  await page.click('.toast .toast-action:has-text("되돌리기")')
  await sleep(300)
  check((await page.locator('.view-live .cmt').count()) === 1, '되돌리기 → 댓글이 제자리에')
  await page.locator('.view-live .cmt').first().hover()
  await page.locator('.view-live .cmt').first().locator('button[title="삭제"]').click()
  await sleep(7000)
  const anns = (await get(`/api/meetings/${mid}/live`)).annotations
  check(!anns.some((a) => a.kind === 'comment'), '되돌리지 않으면 잠시 뒤 서버에서도 지워짐')

  // 4) 새 문장 n개
  await page.waitForFunction(() => { const el = document.querySelector('.pane-scroll'); return el && el.scrollHeight > el.clientHeight + 260 }, null, { timeout: 60000 })
  await page.mouse.move(600, 300)
  await page.mouse.wheel(0, -2000)
  await page.waitForSelector('.jump-pill:has-text("새 문장")', { timeout: 20000 })
  const pill = await page.locator('.jump-pill').innerText()
  await page.screenshot({ path: `${OUT}/u3-new-lines.png` })
  await page.click('.jump-pill')
  await sleep(500)
  const atBottom = await page.evaluate(() => { const el = document.querySelector('.pane-scroll'); return el.scrollHeight - el.scrollTop - el.clientHeight < 140 })
  check(atBottom && (await page.locator('.jump-pill').count()) === 0, `위로 스크롤하면 "${pill}" → 누르면 맨 아래로, 다시 따라감`)

  // 5) ⋯ 보기
  await page.click('.tool-btn[title="보기 설정"]')
  await page.waitForSelector('.view-menu')
  await page.click('.view-menu button:has-text("크게")')
  await page.click('.view-menu button:has-text("댓글 한 줄로 접기")')
  await page.click('.view-menu button:has-text("실제 시각으로 보기")')
  await sleep(200)
  await page.screenshot({ path: `${OUT}/u4-view-menu.png` })
  const t0 = await page.locator('.view-live .u-time').first().innerText()
  check((await page.locator('.transcript.font-l.cmts-collapsed').count()) === 1 && /오전|오후/.test(t0), `보기 설정: 크게 · 댓글 접기 · 실제 시각 (${t0})`)
  await page.click('.view-menu button:has-text("보통")')
  await page.click('.view-menu button:has-text("댓글 한 줄로 접기")')
  await page.click('.view-menu button:has-text("실제 시각으로 보기")')
  await page.keyboard.press('Escape')
  const title = await page.locator('.view-live .u-time').first().getAttribute('title')
  check(/^\d\d:\d\d/.test(await page.locator('.view-live .u-time').first().innerText()) && /오전|오후/.test(title), `녹음 시간 표시, 마우스를 올리면 실제 시각 ("${title}")`)

  // 6) 녹음 종료 → 재생 중 다른 곳을 보면 "재생 위치로"
  await page.click('.btn-stop')
  await page.click('.modal.confirm button:has-text("녹음 종료")')
  await page.waitForSelector('.dock-player', { timeout: 30000 })
  await sleep(500)
  await page.click('.dock-player .btn-round.play')
  await page.waitForFunction(() => document.querySelector('.unit.active'), null, { timeout: 10000 })
  await page.mouse.move(600, 300)
  await page.mouse.wheel(0, 3000)
  await page.waitForSelector('.jump-pill:has-text("재생 위치로")', { timeout: 5000 })
  await page.click('.jump-pill')
  await sleep(600)
  const activeVisible = await page.evaluate(() => {
    const a = document.querySelector('.unit.active')?.getBoundingClientRect()
    const p = document.querySelector('.pane-scroll').getBoundingClientRect()
    return !!a && a.top >= p.top - 4 && a.bottom <= p.bottom + 4
  })
  check(activeVisible && (await page.locator('.jump-pill').count()) === 0, '재생 중 스크롤 → "재생 위치로" → 누르면 재생 중인 문장으로')
  await page.click('.dock-player .btn-round.play')

  // 7) 설정: 저장 공간, 고급은 접혀 있음
  await page.click('.sidebar .icon-btn[title="설정"]')
  await page.waitForSelector('.storage-text')
  const st = await page.locator('.storage-text').innerText()
  check(/회의 \d+개/.test(st) && /남은 공간/.test(st) && (await page.locator('details.advanced[open]').count()) === 0, `설정: 저장 공간 (${st.replace(/\n/g, ' ')}), 엔진 정보는 "고급"에 접혀 있음`)
  await page.screenshot({ path: `${OUT}/u5-settings.png` })
  await page.keyboard.press('Escape')

  // 8) 회의 지우기 → 되돌리기
  await page.click('.page-header .icon-btn[title="더 보기"]')
  await page.click('.menu-item:has-text("회의 삭제")')
  await page.waitForSelector('.toast:has-text("회의를 지웠어요")')
  check((await page.locator(`.sidebar a[href="#/m/${mid}"], .sidebar [data-id="${mid}"]`).count()) === 0 && (await page.locator('.welcome').count()) === 1, '회의 삭제 → 바로 목록에서 사라지고 "되돌리기" 알림')
  await page.click('.toast .toast-action:has-text("되돌리기")')
  await page.waitForSelector('.page-header', { timeout: 5000 })
  await sleep(11000)
  const still = (await get('/api/meetings')).some((m) => m.id === mid)
  check(still && page.url().includes(mid), '되돌리기 → 회의로 돌아오고 지워지지 않음')

  check(errors.length === 0, `콘솔 오류 없음 ${JSON.stringify(errors)}`)
  await browser.close()
})().catch((e) => {
  console.error('E2E FAILED', e)
  process.exit(1)
})
