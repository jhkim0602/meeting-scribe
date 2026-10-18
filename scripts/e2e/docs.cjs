// 브라우저 E2E: 회의 문서 (전사 기록 · 회의록)
//  - ⋯ → 전사 기록 보기: 새 탭, 화자별 발언 위에 회의 중 메모(그 순간)·댓글(번호와 옆 칸)
//  - 위쪽 줄로 회의록 전환: 정보·요약·결정·실행 항목·논의·노트·메모, 방금 쓴 노트까지 담김
//  - 시각을 누르면 그 부분 녹음이 재생되고, 전사 기록에서는 지금 말하는 문장이 표시됨
//  - 위쪽 줄 "파일로 받기": "<날짜> <제목> 전사 기록.html" / "… 회의록.html"
// 서버는 시연용 엔진으로 띄운다 (run.cjs와 같음).
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright')
const OUT = process.env.OUT || 'scripts/e2e/out'
const FAKE_AUDIO = process.env.FAKE_AUDIO || `${OUT}/speech.wav`
const BASE = process.env.BASE || 'http://127.0.0.1:8765'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const get = async (p) => (await fetch(BASE + p)).json()
const send = (method, p, body) => fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => r.json())

function check(cond, msg) {
  if (!cond) throw new Error('확인 실패: ' + msg)
  console.log('✓', msg)
}

const SUMMARY = `## 한 줄 요약
4분기 모바일 앱 리뉴얼은 **11월 18일 베타 출시**로 확정하고, 결제 모듈 교체는 보안 검토가 끝나는 다음 스프린트로 미룬다.

## 주요 논의
- 출시 일정: 디자인 QA가 길어져 2주 미루는 안이 나옴 [00:04]
  - 마케팅 캠페인은 18일 기준으로 다시 짜기로 함
- 결제 모듈: 새 PG사로 바꾸면 수수료가 0.4%p 줄지만 보안 심사에 3주 [00:12]

## 결정 사항
- 베타 출시일은 **11월 18일(월)** 로 확정 [00:09]
- 결제 모듈 교체는 보안 검토 후 다음 스프린트 [00:15]

## 할 일
| 할 일 | 담당 | 기한 | 근거 |
|---|---|---|---|
| 베타 테스터 200명 모집 공고 | 이서연 | 10/24 | [00:10] |
| PG사 보안 심사 서류 제출 | 박준호 | 10/28 | [00:14] |
| 마케팅 일정 재조정 | 미정 | 미정 | [00:06] |

## 남은 질문
- 베타 기간에 기존 사용자 데이터를 이관할지?
`

;(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME || undefined,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${FAKE_AUDIO}`, '--autoplay-policy=no-user-gesture-required'],
  })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['microphone'], colorScheme: 'light', acceptDownloads: true })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + e.message))
  page.on('console', (m) => { if (m.type() === 'error' && !/WebSocket|Failed to load resource/.test(m.text())) errors.push(m.text()) })

  const m = await send('POST', '/api/meetings', { title: '4분기 모바일 앱 리뉴얼 주간 회의' })
  await send('PUT', `/api/meetings/${m.id}/participants`, { participants: ['김민지', '박준호', '이서연'].map((name) => ({ name })) })
  await page.goto(`${BASE}/#/m/${m.id}`)
  await page.click('.btn-record')
  await page.waitForSelector('.dock-rec')
  await page.waitForFunction(() => document.querySelectorAll('.view-live .utext').length >= 3, null, { timeout: 40000 })
  const units = (await get(`/api/meetings/${m.id}/live`)).units
  const u = units[1]
  await send('POST', `/api/meetings/${m.id}/annotations`, { kind: 'comment', view: 'live', body: '이 숫자 재무팀과 맞추기', start: { unit: u.id, offset: 0 }, end: { unit: u.id, offset: u.text.length } })
  await send('POST', `/api/meetings/${m.id}/annotations`, { kind: 'memo', view: 'live', t: units[2].t1 + 0.01, after: units[2].id, body: '대표님 의견 따로 정리' })
  await page.click('.btn-stop')
  await page.click('.modal.confirm button:has-text("녹음 종료")')
  await page.waitForSelector('.dock-player', { timeout: 30000 })
  await send('PUT', `/api/meetings/${m.id}/summary`, { markdown: SUMMARY })

  await send('POST', `/api/meetings/${m.id}/live/speaker`, { segment_ids: [units[0].id], participant_id: (await get(`/api/meetings/${m.id}`)).participants[0].id })

  // 1) 전사 기록: 화자별 발언 위에 메모·댓글
  await page.click('.notes-editor .bn-editor')
  await page.keyboard.type('급히 남긴 노트 한 줄')
  await page.click('.page-header .icon-btn[title="더 보기"]')
  const [doc] = await Promise.all([ctx.waitForEvent('page'), page.click('.menu-item:has-text("전사 기록 보기")')])
  await doc.waitForLoadState('networkidle').catch(() => undefined)
  await doc.waitForSelector('.transcript')
  check(doc.url().endsWith(`/api/meetings/${m.id}/transcript.html`), '⋯ → 전사 기록 보기: 새 탭으로 열림')
  const names = await doc.locator('.turn .who .name').allInnerTexts()
  const tr = await doc.locator('.transcript').innerText()
  check(names[0] === '김민지' && units.every((x) => tr.includes(x.text.slice(0, 8))), `화자별 발언이 이어짐 (${[...new Set(names)].join(', ')})`)
  // 녹음을 마치면 원본(다시 받아 적은 기록)으로 보이므로 문장 번호 대신 시각으로 자리를 본다
  const memoT = units[2].t1 + 0.01
  const around = await doc.evaluate(() => {
    const memo = document.querySelector('.turn .memo')
    const all = [...document.querySelectorAll('.s[data-t], .turn .memo')]
    const i = all.indexOf(memo)
    const prev = all.slice(0, i).filter((x) => x.matches('.s')).pop()
    const next = all.slice(i + 1).find((x) => x.matches('.s'))
    return { prev: prev ? +prev.dataset.t : null, next: next ? +next.dataset.t : null, text: memo?.innerText || '' }
  })
  check(around.text.includes('대표님 의견 따로 정리') && around.prev !== null && around.prev <= memoT && (around.next === null || around.next >= memoT - 0.5),
    `메모는 남긴 순간에 (앞 문장 ${around.prev}초 · 메모 ${memoT.toFixed(1)}초 · 다음 문장 ${around.next}초)`)
  const side = await doc.locator('.side .cmt').first().innerText()
  check(side.includes('이 숫자 재무팀과 맞추기') && (await doc.locator('mark.cm').count()) >= 1 && (await doc.locator('.transcript .cn').first().innerText()) === '1', '댓글은 글자에 번호, 옆 칸에 내용')
  check((await doc.locator('.index li').count()) === 2, '메모·댓글 모아 보기')

  // 2) 시각을 누르면 그 부분 녹음, 지금 말하는 문장 표시
  await doc.waitForSelector('body.can-play', { timeout: 10000 })
  await doc.locator('.turn .who .t').nth(1).click()
  await sleep(1200)
  const st = await doc.evaluate(() => { const a = document.getElementById('au'); return { t: a.currentTime, paused: a.paused, now: document.querySelector('.s.now')?.id } })
  const want = (await doc.locator('.turn').nth(1).locator('.s').first().getAttribute('id'))
  check(!st.paused && st.now === want, `발언 시각을 누르면 그 부분 재생, 지금 문장 표시 (${st.t.toFixed(1)}초)`)
  await doc.evaluate(() => document.getElementById('au').pause())
  await doc.screenshot({ path: `${OUT}/d1-transcript.png`, fullPage: true })

  // 3) 위쪽 줄로 회의록 전환
  await doc.click('.apptools .seg a:has-text("회의록")')
  await doc.waitForSelector('.sec h2')
  const text = await doc.locator('main').innerText()
  const titles = await doc.locator('.sec h2 .ttl').allInnerTexts()
  check(JSON.stringify(titles) === JSON.stringify(['결정 사항', '실행 항목', '주요 논의', '노트', '회의 중 메모·댓글', '남은 질문']), `회의록으로 전환: ${titles.join(' · ')}`)
  check(text.includes('급히 남긴 노트 한 줄'), '방금 쓴 노트까지 담김 (저장을 기다리지 않아도)')
  check(!text.includes(units[0].text) && !text.includes(units[2].text), '회의록에는 받아 적은 문장 자체는 없음')
  await doc.screenshot({ path: `${OUT}/d2-minutes.png`, fullPage: true })

  // 4) 파일로 받기 (테스트용 크롬은 받을 파일 이름을 늘 "download"로 알려 주므로 서버가 준 이름을 본다)
  for (const [tab, label] of [['회의록', '회의록'], ['전사 기록', '전사 기록']]) {
    if (tab === '전사 기록') await doc.click('.apptools .seg a:has-text("전사 기록")')
    const [dl] = await Promise.all([doc.waitForEvent('download'), doc.click('.apptools a:has-text("파일로 받기")')])
    const cd = (await fetch(dl.url())).headers.get('content-disposition') || ''
    const name = decodeURIComponent((cd.match(/filename\*=UTF-8''(.+)$/) || [])[1] || '')
    check(new RegExp(`^\\d{4}-\\d{2}-\\d{2} 4분기 모바일 앱 리뉴얼 주간 회의 ${label}\\.html$`).test(name) && (await dl.failure()) === null, `${tab}: 파일로 받기 "${name}"`)
  }
  await doc.close()

  check(errors.length === 0, `콘솔 오류 없음 ${JSON.stringify(errors)}`)
  await browser.close()
})().catch((e) => {
  console.error('E2E FAILED', e)
  process.exit(1)
})
