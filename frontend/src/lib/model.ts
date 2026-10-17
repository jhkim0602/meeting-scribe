// 기록(unit) + 첨삭(annotation) → 화면에 그릴 조각 목록
import type { Annotation, Unit } from './api'

export type View = 'live' | 'final'

interface RangeAnn {
  s: number
  e: number
  ann: Annotation
  isEnd: boolean
}

export type EditDisplay = 'proof' | 'applied' | 'plain' | 'same' | 'clean-applied' | 'clean-orig'

export type Piece =
  | { type: 'text'; o: number; text: string; anns: Annotation[] }
  | { type: 'edit'; o: number; text: string; ann: Annotation; display: EditDisplay; anns: Annotation[] }

export interface RenderModel {
  perUnit: Map<string, RangeAnn[]>
  memosAfter: Map<string, Annotation[]>
  memosBefore: Annotation[]
  dividersAfter: Map<string, Annotation[]>
  dividersBefore: Annotation[]
  commentEnds: Map<string, Annotation[]>
  unplaced: Annotation[]
}

export function isRange(ann: Annotation): ann is Annotation & { pos: { start: { unit: string; offset: number }; end: { unit: string; offset: number }; approx: boolean; score: number; text: string } } {
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

function editDisplay(ann: Annotation, view: View, showMarks: boolean): EditDisplay {
  if (view === 'live') return showMarks ? 'applied' : 'clean-applied'
  if (!showMarks) return ann.status === 'accepted' ? 'clean-applied' : 'clean-orig'
  if (ann.status === 'accepted') return 'applied'
  if (ann.status === 'dismissed') return 'plain'
  if (ann.match === 'same') return 'same'
  return 'proof'
}

export function piecesFor(text: string, ranges: RangeAnn[] | undefined, view: View, showMarks: boolean): Piece[] {
  if (!ranges || ranges.length === 0) return [{ type: 'text', o: 0, text, anns: [] }]
  // 수정은 겹치지 않게 하나씩만 고른다
  const edits: RangeAnn[] = []
  for (const r of [...ranges].filter((r) => r.ann.kind === 'edit').sort((a, b) => a.s - b.s || b.e - a.e)) {
    if (edits.length && r.s < edits[edits.length - 1].e) continue
    edits.push(r)
  }
  const marks = showMarks ? ranges.filter((r) => r.ann.kind === 'comment' || r.ann.kind === 'highlight') : []
  const bounds = new Set<number>([0, text.length])
  for (const r of [...edits, ...marks]) {
    bounds.add(Math.max(0, Math.min(text.length, r.s)))
    bounds.add(Math.max(0, Math.min(text.length, r.e)))
  }
  const sorted = [...bounds].sort((a, b) => a - b)
  const pieces: Piece[] = []
  let i = 0
  while (i < sorted.length - 1) {
    const a = sorted[i]
    const edit = edits.find((r) => r.s <= a && a < r.e)
    if (edit) {
      const covering = marks.filter((m) => m.s < edit.e && m.e > edit.s).map((m) => m.ann)
      pieces.push({ type: 'edit', o: edit.s, text: text.slice(edit.s, edit.e), ann: edit.ann, display: editDisplay(edit.ann, view, showMarks), anns: covering })
      while (i < sorted.length - 1 && sorted[i] < edit.e) i++
      continue
    }
    const b = sorted[i + 1]
    const anns = marks.filter((m) => m.s <= a && m.e >= b).map((m) => m.ann)
    pieces.push({ type: 'text', o: a, text: text.slice(a, b), anns })
    i++
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
