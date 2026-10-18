// 브라우저 E2E: 가짜 마이크로 녹음 → 댓글(입력창/드래그+C/문장 전체) → 노트로 내보내기 → 문장 직접 고치기(E, 고르고 고치기, 처음대로) → 원본 스크립트(참고용)
// 사용법: 서버를 시연용 엔진으로 띄운 뒤 (README '개발' 참고)
//   OUT=scripts/e2e/out FAKE_AUDIO=scripts/e2e/out/speech.wav node scripts/e2e/run.cjs
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright')
const OUT = process.env.OUT || 'scripts/e2e/out'
const FAKE_AUDIO = process.env.FAKE_AUDIO || `${OUT}/speech.wav`
const BASE = process.env.BASE || 'http://127.0.0.1:8765'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function check(cond, msg) {
  if (!cond) throw new Error('확인 실패: ' + msg)
  console.log('✓', msg)
}

async function selectWords(page, unitIndex, startWord, wordCount) {
  return page.evaluate(({ unitIndex, startWord, wordCount }) => {
    const p = document.querySelectorAll('.transcript .utext')[unitIndex]
    const node = p.querySelector('[data-k="o"]').firstChild
    const words = node.textContent.split(' ')
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
    return node.textContent.slice(a, b)
  }, { unitIndex, startWord, wordCount })
}

async function notesMarkdown(mid) {
  return (await (await fetch(`${BASE}/api/meetings/${mid}/notes`)).json()).markdown
}

;(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME || undefined,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${FAKE_AUDIO}`],
  })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['microphone'], colorScheme: 'light' })
  const page = await ctx.newPage()
  const errors = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + e.message))

  await page.goto(BASE)
  await page.waitForSelector('.welcome h1')
  await page.click('.start-card.primary')
  await page.waitForSelector('.dock-rec', { timeout: 15000 })
  await page.fill('.page-title', '3분기 마케팅 예산 회의')
  await page.keyboard.press('Enter')
  const mid = (await (await fetch(`${BASE}/api/meetings`)).json())[0].id
  await sleep(9000)

  // 1) 녹음 중 아래 입력창: 기본이 '댓글' → 방금 문장에 댓글
  await page.fill('.memo-bar input', '방금 말한 예산 숫자 확인')
  await page.keyboard.press('Enter')
  await sleep(700)
  check((await page.locator('.transcript .cmt').count()) === 1, '입력창으로 방금 문장에 댓글')

  // 2) 메모 모드로 바꿔 메모
  await page.click('.bar-mode button:has-text("메모")')
  await page.fill('.memo-bar input', '여기서 결론 나옴')
  await page.keyboard.press('Enter')
  await sleep(700)
  check((await page.locator('.transcript .memo-card').count()) === 1, '입력창 메모 모드')

  // 3) 드래그 + C 키 → 댓글, '노트에도 추가' 체크
  const q = await selectWords(page, 0, 0, 2)
  await page.waitForSelector('.sel-toolbar')
  await page.screenshot({ path: `${OUT}/c1-toolbar.png` })
  await page.keyboard.press('c')
  await page.waitForSelector('.composer')
  await page.check('.composer .to-notes input')
  await page.fill('.composer textarea', '이 부분 재무팀 확인')
  await page.keyboard.press('Enter')
  await sleep(1500)
  let md = await notesMarkdown(mid)
  check(md.includes('**이 부분 재무팀 확인**') && md.includes(`> ${q}`), '드래그+C 댓글이 노트에도 들어감')
  await page.uncheck('.composer .to-notes input').catch(() => undefined)

  // 4) 문장에 마우스 올려 '이 문장에 댓글' (노트 체크 해제)
  await sleep(4000)
  const unit = page.locator('.transcript .utext').nth(1)
  await unit.hover()
  await page.locator('.transcript .u-actions .u-act[title="이 문장에 댓글"]').click()
  await page.waitForSelector('.composer')
  await page.uncheck('.composer .to-notes input')
  await page.fill('.composer textarea', '채널별 데이터 요청하기')
  await page.keyboard.press('Enter')
  await sleep(800)
  const cmts = await page.locator('.transcript .cmt').count()
  check(cmts === 3, `댓글 3개가 문장 아래에 보임 (${cmts})`)
  await page.screenshot({ path: `${OUT}/c2-live-comments.png` })

  // 5) 댓글 목록 → 아직 안 보낸 것 한 번에 노트로
  await page.click('.pane-head .tool-btn:has-text("댓글")')
  await page.waitForSelector('.ann-list')
  await page.screenshot({ path: `${OUT}/c3-list.png` })
  await page.click('.ann-list-export .btn-primary')
  await sleep(1500)
  await page.keyboard.press('Escape')
  md = await notesMarkdown(mid)
  check(md.includes('### 회의 중 댓글') && md.includes('**방금 말한 예산 숫자 확인**') && md.includes('메모 **여기서 결론 나옴**') && md.includes('**채널별 데이터 요청하기**'), '나머지 댓글·메모를 한 번에 노트로')
  check((md.match(/이 부분 재무팀 확인/g) || []).length === 1, '이미 보낸 댓글은 다시 넣지 않음')
  check((await page.locator('.transcript .cmt .chip-ok').count()) === 3, '댓글에 "노트" 표시')

  // 6) 답글 + 해결
  const first = page.locator('.transcript .cmt').first()
  await first.hover()
  await first.locator('button[title="답글"]').click()
  await page.fill('.transcript .cmt input.cmt-input', '다음 회의 때 다시')
  await page.keyboard.press('Enter')
  await sleep(600)
  check((await page.locator('.transcript .cmt-reply').count()) === 1, '답글')

  // 7) 녹음 중에 틀린 글자를 그 자리에서 고친다: 문장에 마우스를 올리고 E
  const live0 = (await (await fetch(`${BASE}/api/meetings/${mid}/live`)).json()).units
  const u0 = live0[0]
  await page.locator('.transcript .utext').first().hover()
  await page.keyboard.press('e')
  await page.waitForSelector('.transcript .utext.editing')
  await page.keyboard.press('Home')
  await page.keyboard.type('[고침] ')
  await page.screenshot({ path: `${OUT}/c4-editing.png` })
  await page.keyboard.press('Enter')
  await page.waitForFunction(() => !document.querySelector('.utext.editing'))
  await sleep(500)
  let after0 = (await (await fetch(`${BASE}/api/meetings/${mid}/live`)).json()).units[0]
  check(after0.text === `[고침] ${u0.text}` && after0.text_orig === u0.text && (await page.locator('.transcript .utext.edited').count()) === 1,
    `녹음 중 E로 바로 고침 ("${after0.text.slice(0, 16)}…")`)
  const anns0 = (await (await fetch(`${BASE}/api/meetings/${mid}/live`)).json()).annotations
  const c0 = anns0.find((a) => a.body === '이 부분 재무팀 확인')
  check(c0 && c0.pos.text === q, `고쳐도 댓글은 같은 글자에 ("${c0?.pos.text}")`)

  // 8) 글자를 고르고 "고치기" → 고른 부분만 바로 바꿔 쓰기
  const word2 = await selectWords(page, 1, 1, 1)
  await page.waitForSelector('.sel-toolbar')
  await page.click('.sel-toolbar button:has-text("고치기")')
  await page.waitForSelector('.transcript .utext.editing')
  await page.keyboard.type('스무')
  await page.keyboard.press('Enter')
  await sleep(600)
  const u1 = (await (await fetch(`${BASE}/api/meetings/${mid}/live`)).json()).units[1]
  check(u1.text_orig && u1.text === u1.text_orig.replace(word2, '스무'), `고른 글자 "${word2}" → "스무"`)

  // 9) 처음 받아 적은 대로 되돌리기
  await page.locator('.transcript .utext').nth(1).hover()
  await page.keyboard.press('e')
  await page.waitForSelector('.edit-orig')
  await page.click('.edit-orig')
  await sleep(600)
  const u1b = (await (await fetch(`${BASE}/api/meetings/${mid}/live`)).json()).units[1]
  check(u1b.text === u1.text_orig && !('text_orig' in u1b), '"처음 받아 적은 대로" → 원래 글자, 고친 표시도 사라짐')
  // Esc는 고치지 않고 닫기
  await page.locator('.transcript .utext').nth(1).hover()
  await page.keyboard.press('e')
  await page.keyboard.type('지울 글자')
  await page.keyboard.press('Escape')
  await sleep(300)
  check((await (await fetch(`${BASE}/api/meetings/${mid}/live`)).json()).units[1].text === u1.text_orig, 'Esc → 고치지 않음')

  // 10) 녹음 종료 → 기록은 그대로, 원본 스크립트는 참고용으로 따로
  await page.click('.btn-stop')
  await page.click('.modal.confirm button:has-text("녹음 종료")')
  await page.waitForSelector('.dock-player', { timeout: 30000 })
  const tabNames = (await page.locator('.tab').allInnerTexts()).map((x) => x.replace(/[\d\s●]+/g, ''))
  check(JSON.stringify(tabNames) === JSON.stringify(['기록', 'AI요약']), `탭은 기록 · AI 요약 (원본 탭 없음: ${tabNames.join(', ')})`)
  for (let i = 0; i < 60; i++) {
    const job = (await (await fetch(`${BASE}/api/meetings/${mid}/job`)).json()).job
    if (job && job.status === 'done') break
    await sleep(1000)
  }
  await sleep(1500)
  after0 = (await (await fetch(`${BASE}/api/meetings/${mid}/live`)).json()).units[0]
  check(after0.text === `[고침] ${u0.text}` && (await page.locator('.transcript .cmt').count()) === 3, '후처리 뒤에도 고친 글자와 댓글 3개가 기록에 그대로')
  await page.click('.page-header .icon-btn[title="더 보기"]')
  check((await page.locator('.menu-item:has-text("원본 스크립트 받기")').count()) === 1, '⋯ → 원본 스크립트 받기 (.md)')
  await page.keyboard.press('Escape')
  const script = await (await fetch(`${BASE}/api/meetings/${mid}/script.md`)).text()
  check(script.includes('원본 스크립트') && !script.includes('[고침]'), '원본 스크립트는 기록과 섞이지 않음')
  await page.screenshot({ path: `${OUT}/c5-record-after.png` })

  // 11) 댓글 하이라이트를 누르면 해당 댓글로 이동
  await page.locator('.transcript .mk-comment').first().click()
  await sleep(300)
  check((await page.locator('.transcript .cmt.flash').count()) === 1, '하이라이트 클릭 → 댓글 강조')
  check((await page.locator('.dock-player .scrub-mark.mk-comment').count()) === 3, '재생 막대에 댓글 눈금 3개')

  // 12) ⌘F: 기록 안에서 찾기 (띄어쓰기 무시), 고친 글자도 찾아짐, Esc로 닫기
  await page.mouse.click(600, 400)
  await page.keyboard.press('Control+f')
  await page.waitForSelector('.find-bar input')
  await page.keyboard.type('고침')
  await page.waitForFunction(() => /\d+\/\d+/.test(document.querySelector('.find-count')?.textContent || ''), null, { timeout: 5000 })
  const painted = await page.evaluate(() => CSS.highlights?.get('scribe-find-current')?.size ?? 0)
  check(painted > 0, `⌘F 찾기 "고침" → ${await page.locator('.find-count').innerText()}, 글자 칠함`)
  await page.screenshot({ path: `${OUT}/c6-find.png` })
  await page.keyboard.press('Escape')
  check((await page.locator('.find-bar').count()) === 0 && (await page.evaluate(() => CSS.highlights?.has('scribe-find') ?? false)) === false, 'Esc → 찾기 닫고 칠한 것 지움')

  await page.click('.theme-switch button[title="어둡게"]')
  await sleep(400)
  await page.screenshot({ path: `${OUT}/c7-dark.png` })
  console.log('노트:\n' + (await notesMarkdown(mid)))
  check(errors.length === 0, `콘솔 오류 없음 ${JSON.stringify(errors)}`)
  await browser.close()
})().catch((e) => {
  console.error('E2E FAILED', e)
  process.exit(1)
})
