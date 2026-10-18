// 기록(unit) + 메모·댓글·강조(annotation) → 화면에 그릴 조각 목록
import type { Annotation, Unit } from './api'

export interface RangeAnn {
  s: number
  e: number
  ann: Annotation
  isEnd: boolean
}

export interface Piece {
  o: number
  text: string
  anns: Annotation[]
}

export interface RenderModel {
  perUnit: Map<string, RangeAnn[]>
  memosAfter: Map<string, Annotation[]>
  memosBefore: Annotation[]
  dividersAfter: Map<string, Annotation[]>
  dividersBefore: Annotation[]
  commentEnds: Map<string, Annotation[]>
  unplaced: Annotation[]
}

export function isRange(ann: Annotation): ann is Annotation & { pos: { start: { unit: string; offset: number }; end: { unit: string; offset: number }; text: string } } {
  return !!ann.pos && 'start' in ann.pos
}

export function buildModel(units: Unit[], annotations: Annotation[]): RenderModel {
  const order = new Map(units.map((u, i) => [u.id, i]))
  const perUnit = new Map<string, RangeAnn[]>()
  const memosAfter = new Map<string, Annotation[]>()
  const memosBefore: Annotation[] = []
  const dividersAfter = new Map<string, Annotation[]>()
  const dividersBefore: Annotation[] = []
  const commentEnds = new Map<string, Annotation[]>()
  const unplaced: Annotation[] = []
  for (const ann of annotations) {
    if (!ann.pos) {
      unplaced.push(ann)
      continue
    }
    if (ann.kind === 'divider' && 'after' in ann.pos) {
      const key = ann.pos.after
      if (key === null) dividersBefore.push(ann)
      else dividersAfter.set(key, [...(dividersAfter.get(key) || []), ann])
      continue
    }
    if (ann.kind === 'memo' && 'after' in ann.pos) {
      const key = ann.pos.after
      if (key === null) memosBefore.push(ann)
      else memosAfter.set(key, [...(memosAfter.get(key) || []), ann])
      continue
    }
    if (!isRange(ann)) continue
    const su = order.get(ann.pos.start.unit)
    const eu = order.get(ann.pos.end.unit)
    if (su === undefined || eu === undefined) {
      unplaced.push(ann)
      continue
    }
    for (let i = su; i <= eu; i++) {
      const u = units[i]
      const s = i === su ? ann.pos.start.offset : 0
      const e = i === eu ? ann.pos.end.offset : u.text.length
      if (e > s) perUnit.set(u.id, [...(perUnit.get(u.id) || []), { s, e, ann, isEnd: i === eu }])
    }
    if (ann.kind === 'comment') {
      const endId = ann.pos.end.unit
      commentEnds.set(endId, [...(commentEnds.get(endId) || []), ann])
    }
  }
  for (const list of memosAfter.values()) list.sort((a, b) => (a.anchor.t ?? 0) - (b.anchor.t ?? 0))
  for (const list of commentEnds.values()) list.sort((a, b) => (a.anchor.t0 ?? 0) - (b.anchor.t0 ?? 0) || a.created_at - b.created_at)
  for (const list of dividersAfter.values()) list.sort((a, b) => (a.anchor.t ?? 0) - (b.anchor.t ?? 0))
  return { perUnit, memosAfter, memosBefore, dividersAfter, dividersBefore, commentEnds, unplaced }
}

export function piecesFor(text: string, ranges: RangeAnn[] | undefined, showMarks: boolean): Piece[] {
  const marks = showMarks && ranges ? ranges.filter((r) => r.ann.kind === 'comment' || r.ann.kind === 'highlight') : []
  if (!marks.length) return [{ o: 0, text, anns: [] }]
  const bounds = new Set<number>([0, text.length])
  for (const r of marks) {
    bounds.add(Math.max(0, Math.min(text.length, r.s)))
    bounds.add(Math.max(0, Math.min(text.length, r.e)))
  }
  const sorted = [...bounds].sort((a, b) => a - b)
  const pieces: Piece[] = []
  for (let i = 0; i < sorted.length - 1; i++) {
    const a = sorted[i]
    const b = sorted[i + 1]
    pieces.push({ o: a, text: text.slice(a, b), anns: marks.filter((m) => m.s <= a && m.e >= b).map((m) => m.ann) })
  }
  return pieces
}

export function unitAtTime(units: { t0: number; t1: number; id: string }[], t: number): string | null {
  let lo = 0
  let hi = units.length - 1
  let ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (units[mid].t0 <= t) {
      ans = mid
      lo = mid + 1
    } else hi = mid - 1
  }
  if (ans < 0) return null
  const u = units[ans]
  return t <= u.t1 + 1.5 ? u.id : null
}

export function timeAtOffset(u: Unit, offset: number): number {
  if (u.words && u.words.length) {
    for (const [o, l, s, e] of u.words) {
      if (offset < o) return s
      if (offset < o + l) return s + ((e - s) * (offset - o)) / Math.max(1, l)
    }
    return u.words[u.words.length - 1][3]
  }
  const n = Math.max(1, u.text.length)
  return u.t0 + ((u.t1 - u.t0) * Math.min(offset, n)) / n
}
