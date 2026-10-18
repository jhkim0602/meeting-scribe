// 브라우저 E2E: 실시간 임시 화자
//  - 참여자를 미리 넣기 (쉼표로 여러 명), 빼기 → 되돌리기
//  - 목소리로 묶인 단락(화자 A, B)에 칩을 눌러 참여자 고르기 → 같은 목소리 문장에도 바로 반영 → "이 문장만"으로 되돌리기
//  - 문장에 마우스를 올리고 숫자키 / Shift+숫자키(그 문장만)
//  - 앞으로 오는 같은 목소리 문장은 처음부터 그 참여자
//  - 같은 사람이 이어 말한 문장은 한 단락, 녹음을 마쳐도 고른 화자가 기록에 그대로
//  - 다음 회의: 같은 이름을 넣으면 기억한 목소리로 처음부터 알아봄
// 서버는 빈 데이터 폴더와 시연용 엔진(목소리 시간표: A 0~9.5초, B 9.5~14.5초, A 14.5~21.5초, C 21.5~27.5초 …)으로 띄운다.
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
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${FAKE_AUDIO}`],
  })
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['microphone'], colorScheme: 'light' })).newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + e.message))
  page.on('console', (m) => { if (m.type() === 'error' && !/WebSocket|Failed to load resource/.test(m.text())) errors.push(m.text()) })
  const chips = () => page.locator('.transcript .spk-chip').allInnerTexts()

  // 1) 참여자를 미리 넣기
  const m = await (await fetch(`${BASE}/api/meetings`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"title":"주간 회의"}' })).json()
  await page.goto(`${BASE}/#/m/${m.id}`)
  await page.click('.people-empty')
  await page.fill('.people-input', '김민지, 박준호')
  await page.keyboard.press('Enter')
  await sleep(400)
  await page.keyboard.press('Escape')
  check(JSON.stringify(await page.locator('.person').allInnerTexts()) === '["김민지","박준호"]', '참여자 미리 넣기 (쉼표로 여러 명)')

  // 2) 녹음: 목소리로 단락이 묶인다
  await page.click('.btn-record')
  await page.waitForSelector('.dock-rec')
  await page.waitForFunction(() => [...document.querySelectorAll('.transcript .spk-chip')].filter((c) => !c.classList.contains('unknown') || c.textContent !== '화자?').length >= 3, null, { timeout: 40000 })
  await sleep(300)
  const c1 = await chips()
  check(c1[0] === '화자 A' && c1[1] === '화자 B' && c1[2] === '화자 A', `목소리로 임시 화자 표시 (${c1.join(', ')})`)
  const turns = await page.locator('.transcript .turn').count()
  const sentences = await page.locator('.transcript .utext').count()
  check(turns < sentences, `같은 사람이 이어 말한 문장은 한 단락 (문장 ${sentences}개 → 단락 ${turns}개)`)
  await page.screenshot({ path: `${OUT}/s1-temporary.png` })

  // 3) 칩을 눌러 고르면 같은 목소리 문장에도 바로
  await page.locator('.transcript .spk-chip').first().click()
  await page.waitForSelector('.speaker-picker')
  await page.screenshot({ path: `${OUT}/s2-picker.png` })
  await page.click('.speaker-picker .picker-item:has-text("김민지")')
  await page.waitForSelector('.toast:has-text("같은 목소리")')
  const c2 = await chips()
  check(c2[0] === '김민지' && c2.filter((x) => x.startsWith('김민지')).length >= 2 && (await page.locator('.transcript .spk-chip.manual').count()) === 1, `한 번 고르면 같은 목소리 단락도 김민지 (${c2.join(', ')})`)
  await page.screenshot({ path: `${OUT}/s3-propagated.png` })
  await page.click('.toast .toast-action:has-text("이 문장만")')
  await sleep(500)
  const c3 = await chips()
  check(c3[0] === '김민지' && c3.includes('화자 A'), `"이 문장만" → 나머지는 예전대로 (${c3.join(', ')})`)

  // 4) 숫자키: 마우스를 올린 문장의 단락 (같은 목소리도 함께)
  await page.locator('.transcript .turn').filter({ has: page.locator('.spk-chip:has-text("화자 B")') }).first().locator('.utext').first().hover()
  await page.keyboard.press('2')
  await sleep(500)
  check((await chips()).includes('박준호'), '문장에 마우스를 올리고 2 → 박준호')
  await page.locator('.transcript .utext').first().hover()
  await page.keyboard.press('1')
  await sleep(400)

  // 5) Shift+숫자: 그 문장만 박준호 → 첫 단락에서 빠진다
  const firstTurn = page.locator('.transcript .turn').first()
  const n0 = await firstTurn.locator('.utext').count()
  const lastId = (await firstTurn.locator('.utext').last().getAttribute('data-unit'))
  await firstTurn.locator('.utext').last().hover()
  await page.keyboard.press('Shift+Digit2')
  await sleep(600)
  const junho = (await get(`/api/meetings/${m.id}`)).participants[1].id
  const one = (await get(`/api/meetings/${m.id}/live`)).units.find((u) => u.id === lastId)
  const owner = await page.locator(`#unit-${lastId}`).evaluate((el) => {
    let t = el.closest('.vrow')
    while (t && !t.querySelector('.spk-chip')) t = t.previousElementSibling
    return t?.querySelector('.spk-chip')?.textContent || ''
  })
  check(one.speaker === junho && one.speaker_src === 'manual' && (await page.locator('.transcript .turn').first().locator('.utext').count()) === n0 - 1 && owner.startsWith('박준호'),
    `Shift+2 → 그 문장만 박준호 (첫 단락 ${n0}→${n0 - 1}문장, 박준호 단락으로)`)
  await page.screenshot({ path: `${OUT}/s4-assigned.png` })

  // 6) 앞으로 오는 같은 목소리(A) 문장은 처음부터 김민지 (시간표상 35.5초부터 다시 A)
  const minji = (await get(`/api/meetings/${m.id}`)).participants[0].id
  const before = (await get(`/api/meetings/${m.id}/live`)).units.length
  let fresh = []
  for (let i = 0; i < 45 && !fresh.length; i++) {
    await sleep(1000)
    fresh = (await get(`/api/meetings/${m.id}/live`)).units.slice(before).filter((u) => u.speaker_auto === 'A' && u.speaker_src !== 'manual')
  }
  check(fresh.length > 0 && fresh.every((u) => u.speaker === minji && u.speaker_src === 'auto'), `새로 들어온 같은 목소리 문장도 처음부터 김민지 (${fresh.length}개)`)
  check((await chips()).slice(-1)[0].startsWith('김민지'), '화면의 마지막 단락도 김민지 (추정 표시)')

  // 7) 참여자 빼기 → 되돌리기
  await page.locator('.person:has-text("박준호")').hover()
  await page.locator('.person:has-text("박준호") .person-x').click()
  await page.waitForSelector('.toast:has-text("참여자에서 뺐어요")')
  await page.click('.toast .toast-action:has-text("되돌리기")')
  await sleep(500)
  check((await page.locator('.person').count()) === 2, '참여자 빼기 → 되돌리기')

  // 8) 녹음 종료 → 원본 스크립트를 만들어도 고른 화자는 기록에 그대로, 끝난 뒤에도 같은 칩으로 고르기
  const chipsBefore = await chips()
  await page.click('.btn-stop')
  await page.click('.modal.confirm button:has-text("녹음 종료")')
  await page.waitForSelector('.dock-player', { timeout: 30000 })
  for (let i = 0; i < 60; i++) {
    const job = (await get(`/api/meetings/${m.id}/job`)).job
    if (job && job.status === 'done') break
    await sleep(1000)
  }
  await sleep(1200)
  const chipsAfter = await chips()
  check(JSON.stringify(chipsAfter.slice(0, chipsBefore.length)) === JSON.stringify(chipsBefore) && chipsAfter.includes('박준호'), `녹음을 마쳐도 고른 화자가 기록에 그대로 (${[...new Set(chipsAfter)].join(', ')})`)
  await page.locator('.transcript .spk-chip').first().click()
  await page.waitForSelector('.speaker-picker')
  check((await page.locator('.speaker-picker .picker-item').count()) === 2, '끝난 뒤에도 같은 고르기 창')
  await page.screenshot({ path: `${OUT}/s5-after-picker.png` })
  await page.keyboard.press('Escape')

  // 9) 다음 회의: 같은 이름을 넣으면 기억한 목소리로 처음부터 알아본다
  const m2 = await (await fetch(`${BASE}/api/meetings`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"title":"다음 회의"}' })).json()
  await page.goto(`${BASE}/#/m/${m2.id}`)
  await page.click('.people-empty')
  await page.fill('.people-input', '김민지')
  await page.keyboard.press('Enter')
  await page.keyboard.press('Escape')
  await page.click('.btn-record')
  await page.waitForFunction(() => document.querySelectorAll('.transcript .spk-chip').length >= 1, null, { timeout: 30000 })
  const first = (await chips())[0]
  check(first.startsWith('김민지') && (await page.locator('.transcript .spk-chip.guess').count()) >= 1, `다음 회의: 기억한 목소리로 처음부터 "${first.replace(/\s+/g, '')}" (추정 표시)`)
  await page.screenshot({ path: `${OUT}/s6-memory.png` })
  await page.click('.btn-stop')
  await page.click('.modal.confirm button:has-text("녹음 종료")')
  await page.waitForSelector('.dock-player', { timeout: 30000 })

  check(errors.length === 0, `콘솔 오류 없음 ${JSON.stringify(errors)}`)
  await browser.close()
})().catch((e) => {
  console.error('E2E FAILED', e)
  process.exit(1)
})
