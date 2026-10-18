// 문장(unit)들을 화자 단락(turn)으로 묶는다.
// 같은 사람이 잠깐 멈췄다 이어 말한 문장, 말끝이 아닌데 끊긴 문장은 한 단락으로 이어 보여 준다.
import type { Unit } from './api'

export interface Turn {
  id: string // 첫 문장 id
  start: number // units 안에서 첫 문장 번호
  units: Unit[]
  key: string | null // 화자 (같으면 같은 단락)
}

const FINAL_MARKS = /[.?!。…？！]["'”’)]*$/
const FINAL_ENDINGS = /(다|요|죠|까|네|군|래|지요|니다|세요)["'”’)]*$/

/** 문장이 말끝으로 끝났는지 (끝나지 않았으면 다음 문장과 잇는다) */
export function endsSentence(text: string): boolean {
  const t = text.trim()
  return FINAL_MARKS.test(t) || FINAL_ENDINGS.test(t)
}

export function speakerKey(u: Unit): string | null {
  if (u.speaker) return `p:${u.speaker}`
  if (u.speaker_auto) return `a:${u.speaker_auto}`
  return null
}

const MAX_UNITS = 8
const MAX_SPAN_S = 90

/** breaks: 이 문장 뒤에서는 단락을 끊는다 (구분선, 메모)
 *  pending: 방금 들어와 화자를 기다리는 문장 (잠깐 앞 단락에 이어 둬 깜빡이지 않게) */
export function buildTurns(units: Unit[], breaks: Set<string>, pending: string | null = null): Turn[] {
  const turns: Turn[] = []
  let cur: Turn | null = null
  units.forEach((u, i) => {
    const key = speakerKey(u)
    const prev = i > 0 ? units[i - 1] : null
    let join = false
    if (cur && prev && !breaks.has(prev.id) && cur.units.length < MAX_UNITS && u.t1 - cur.units[0].t0 <= MAX_SPAN_S) {
      const gap = u.t0 - prev.t1
      if (u.id === pending) join = gap <= 4
      else if (key !== null && key === cur.key) join = gap <= 4
      else if (key === null && cur.key === null) join = gap <= 1.6 && !endsSentence(prev.text)
    }
    if (join && cur) cur.units.push(u)
    else {
      cur = { id: u.id, start: i, units: [u], key }
      turns.push(cur)
    }
  })
  return turns
}
