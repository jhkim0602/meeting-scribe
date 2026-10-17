// 브라우저 선택 영역 → (unit, 글자 위치). 각 글자 조각 span에는 data-o(원문 위치), data-k(o=원문, r=수정문)가 있다.
import type { Pos } from './api'

export interface TextSelection {
  start: Pos
  end: Pos
  text: string
  rect: DOMRect
}

function spanPos(span: HTMLElement): { o: number; k: string; len: number } {
  return { o: Number(span.dataset.o), k: span.dataset.k || 'o', len: Number(span.dataset.len || span.textContent?.length || 0) }
}

function boundary(node: Node, offset: number, utext: HTMLElement, side: 'start' | 'end'): number {
  if (node.nodeType === Node.TEXT_NODE) {
    const span = node.parentElement?.closest<HTMLElement>('[data-o]')
    if (span && utext.contains(span)) {
      const p = spanPos(span)
      if (p.k === 'r') return side === 'start' ? p.o : p.o + p.len
      return p.o + Math.min(offset, p.len)
    }
  }
  const el = node.nodeType === Node.ELEMENT_NODE ? (node as HTMLElement) : node.parentElement
  if (!el) return 0
  const spans = Array.from(utext.querySelectorAll<HTMLElement>('[data-o]'))
  if (!spans.length) return 0
  // node의 offset번째 자식 앞이 경계. 그 뒤에 처음 나오는 span을 찾는다.
  const child = el.childNodes[offset]
  if (child) {
    const after = spans.find((s) => child === s || child.contains(s) || (child.compareDocumentPosition(s) & Node.DOCUMENT_POSITION_FOLLOWING))
    if (after) return spanPos(after).o
  }
  const before = [...spans].reverse().find((s) => el === s || el.contains(s) || (s.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING))
  if (before) {
    const p = spanPos(before)
    return p.o + p.len
  }
  return 0
}

export function readSelection(container: HTMLElement): TextSelection | null {
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null
  const range = sel.getRangeAt(0)
  if (!container.contains(range.commonAncestorContainer)) return null
  const texts = Array.from(container.querySelectorAll<HTMLElement>('.utext')).filter((el) => range.intersectsNode(el))
  if (!texts.length) return null
  const first = texts[0]
  const last = texts[texts.length - 1]
  const startOff = first.contains(range.startContainer) ? boundary(range.startContainer, range.startOffset, first, 'start') : 0
  const endOff = last.contains(range.endContainer) ? boundary(range.endContainer, range.endOffset, last, 'end') : Number(last.dataset.len)
  const start = { unit: first.dataset.unit!, offset: startOff }
  const end = { unit: last.dataset.unit!, offset: endOff }
  if (start.unit === end.unit && end.offset <= start.offset) return null
  const text = sel.toString().trim()
  if (!text) return null
  return { start, end, text, rect: range.getBoundingClientRect() }
}
