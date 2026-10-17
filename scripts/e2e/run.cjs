// 브라우저 E2E: 가짜 마이크로 녹음 → 댓글/수정/메모 → 원본 → 첨삭 재배치 확인
// 사용법: 서버를 시연용 엔진으로 띄운 뒤 (README '개발' 참고)
//   OUT=scripts/e2e/out FAKE_AUDIO=scripts/e2e/out/speech.wav node scripts/e2e/run.cjs
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright')
const OUT = process.env.OUT || 'scripts/e2e/out'
const FAKE_AUDIO = process.env.FAKE_AUDIO || `${OUT}/speech.wav`
const BASE = process.env.BASE || 'http://127.0.0.1:8765'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function selectText(page, unitIndex, startWord, wordCount) {
  // unitIndex번째 기록 문장에서 startWord번째 단어부터 wordCount개 단어를 선택하고 mouseup
  return page.evaluate(({ unitIndex, startWord, wordCount }) => {
    const p = document.querySelectorAll('.utext')[unitIndex]
    const span = p.querySelector('[data-k="o"]')
    const node = span.firstChild
    const text = node.textContent
    const words = text.split(' ')
    startWord = Math.min(startWord, Math.max(0, words.length - 1))
    wordCount = Math.max(1, Math.min(wordCount, words.length - startWord))
    let a = 0
    for (let i = 0; i < startWord; i++) a += words[i].length + 1
    let b = a
    for (let i = startWord; i < startWord + wordCount; i++) b += words[i].length + (i > startWord ? 1 : 0)
    const r = document.createRange()
    r.setStart(node, a)
    r.setEnd(node, b)
    const sel = window.getSelection()
    sel.removeAllRanges()
    sel.addRange(r)
    p.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    return text.slice(a, b)
  }, { unitIndex, startWord, wordCount })
}

;(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME || undefined,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${FAKE_AUDIO}`, '--autoplay-policy=no-user-gesture-required'],
  })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['microphone'], colorScheme: 'light' })
  const page = await ctx.newPage()
  const errors = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + e.message))

  await page.goto(BASE)
  await page.waitForSelector('.welcome h1')
  await page.screenshot({ path: `${OUT}/01-welcome.png` })

  await page.click('.start-card.primary')
  await page.waitForSelector('.dock-rec', { timeout: 15000 })
  await page.fill('.page-title', '3분기 마케팅 예산 회의')
  await page.keyboard.press('Enter')
  await sleep(13000)
  const n1 = await page.locator('.view-live .unit:not(.partial)').count()
  console.log('live units after 13s:', n1)
  await page.screenshot({ path: `${OUT}/02-recording.png` })

  // 지금 메모
  await page.fill('.memo-bar input', '예산 숫자 다시 확인하기')
  await page.keyboard.press('Enter')
  await sleep(600)

  // 댓글
  const q1 = await selectText(page, 0, 1, 2)
  console.log('comment on:', q1)
  await page.waitForSelector('.sel-toolbar')
  await page.screenshot({ path: `${OUT}/03-selection.png` })
  await page.click('.sel-toolbar button:has-text("댓글")')
  await page.fill('.composer textarea', '이 숫자 맞는지 재무팀 확인')
  await page.keyboard.press('Enter')
  await sleep(600)

  // 수정
  const q2 = await selectText(page, 1, 0, 2)
  console.log('edit on:', q2)
  await page.waitForSelector('.sel-toolbar')
  await page.click('.sel-toolbar button:has-text("수정")')
  await page.fill('.composer textarea', q2.replace(/ /, ' (수정) '))
  await page.keyboard.press('Enter')
  await sleep(600)

  // 노트로 보내기
  await selectText(page, 2, 0, 3)
  await page.waitForSelector('.sel-toolbar')
  await page.click('.sel-toolbar button:has-text("노트로")')
  await sleep(500)

  // 노트 작성
  await page.click('.notes-editor .bn-editor')
  await page.keyboard.press('Control+End')
  await page.keyboard.type('결정: 영상 광고 예산을 줄이고 검색 광고로 옮긴다 ')
  await page.click('.notes-toolbar button:has-text("시각 넣기")')
  await sleep(1500)
  await page.screenshot({ path: `${OUT}/04-annotated-live.png` })

  // 녹음 종료 → 원본
  await page.click('.btn-stop')
  await page.waitForSelector('.dock-player', { timeout: 30000 })
  await page.click('.tab:has-text("원본")')
  await page.waitForSelector('.view-final .unit', { timeout: 60000 })
  await sleep(800)
  await page.screenshot({ path: `${OUT}/05-final.png` })
  const finalInfo = await page.evaluate(() => ({
    units: document.querySelectorAll('.view-final .unit').length,
    speakers: [...document.querySelectorAll('.speakers-legend .sp-name')].map((e) => e.textContent),
    memos: document.querySelectorAll('.view-final .memo-card').length,
    comments: document.querySelectorAll('.view-final .mk-comment').length,
    proof: document.querySelectorAll('.view-final .mk-edit.proof').length,
    moved: document.querySelector('.moved-note')?.textContent,
  }))
  console.log('final:', JSON.stringify(finalInfo))

  // 수정 첨삭 열기 → 원본에 적용
  const proof = page.locator('.view-final .mk-edit').first()
  if (await proof.count()) {
    await proof.click()
    await page.waitForSelector('.ann-pop')
    await page.screenshot({ path: `${OUT}/06-edit-popover.png` })
    const apply = page.locator('.ann-pop button:has-text("원본에 적용")')
    if (await apply.count()) {
      await apply.click()
      await sleep(600)
    }
    await page.keyboard.press('Escape')
  }
  // 댓글 팝업
  const c = page.locator('.view-final .comment-badge').first()
  if (await c.count()) {
    await c.click()
    await page.waitForSelector('.ann-pop')
    await page.screenshot({ path: `${OUT}/07-comment-popover.png` })
    await page.keyboard.press('Escape')
  }

  // 화자 이름 바꾸기
  await page.click('.speakers-legend .sp-name >> nth=0')
  await page.fill('.sp-input', '김민지')
  await page.keyboard.press('Enter')
  await sleep(600)

  // AI 요약 탭
  await page.click('.tab:has-text("AI 요약")')
  await sleep(300)
  await page.screenshot({ path: `${OUT}/08-summary-empty.png` })

  // 다크 모드
  await page.click('.tab:has-text("원본")')
  await page.click('.theme-switch button[title="어둡게"]')
  await sleep(500)
  await page.screenshot({ path: `${OUT}/09-final-dark.png` })

  // 노트 저장 확인
  const notes = await (await fetch(`${BASE}/api/meetings`)).json()
  const mid = notes[0].id
  const md = await (await fetch(`${BASE}/api/meetings/${mid}/notes`)).json()
  console.log('notes md:', JSON.stringify(md.markdown))
  const exp = await (await fetch(`${BASE}/api/meetings/${mid}/export.md`)).text()
  console.log('export head:\n' + exp.split('\n').slice(0, 30).join('\n'))
  console.log('console errors:', JSON.stringify(errors, null, 1))
  await browser.close()
})().catch((e) => {
  console.error('E2E FAILED', e)
  process.exit(1)
})
