// 아이콘 그림(svg) → png. 바꾼 뒤: node launcher/design/render.cjs && cd launcher && npx tauri icon design/icon.png
// (tray.png는 src-tauri/icons/tray.png로 복사). playwright가 필요해요: PLAYWRIGHT=경로, CHROMIUM=실행 파일로 바꿀 수 있어요.
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright')
const fs = require('fs')
;(async () => {
  const b = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {})
  for (const [svg, out, size] of [['icon.svg', 'icon.png', 1024], ['tray.svg', 'tray.png', 44]]) {
    const p = await (await b.newContext({ viewport: { width: size, height: size }, deviceScaleFactor: 1 })).newPage()
    await p.setContent(`<html><body style="margin:0;background:transparent">${fs.readFileSync(__dirname + '/' + svg, 'utf8').replace('<svg ', `<svg width="${size}" height="${size}" `)}</body></html>`)
    await p.screenshot({ path: __dirname + '/' + out, omitBackground: true })
  }
  await b.close()
})()
