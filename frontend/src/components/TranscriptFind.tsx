import { ChevronDown, ChevronUp, List, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Unit } from '../lib/api'
import { fmtTime } from '../lib/time'

export interface Hit {
  i: number // 문장 번호
  s: number
  e: number
}

const MAX_HITS = 2000

function escapeRe(c: string) {
  return c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** 띄어쓰기는 무시하고 찾는다 ("예산숫자"로 "예산 숫자"도 찾음) */
export function findHits(units: Unit[], query: string): Hit[] {
  const chars = [...query.replace(/\s+/g, '')]
  if (!chars.length) return []
  const re = new RegExp(chars.map(escapeRe).join('\\s*'), 'gi')
  const hits: Hit[] = []
  for (let i = 0; i < units.length; i++) {
    const text = units[i].text
    re.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = re.exec(text))) {
      hits.push({ i, s: m.index, e: m.index + m[0].length })
      if (hits.length >= MAX_HITS) return hits
    }
  }
  return hits
}

type HighlightRegistry = { set: (name: string, h: unknown) => void; delete: (name: string) => void }
type HighlightCtor = new (...ranges: Range[]) => unknown

function registry(): HighlightRegistry | null {
  const reg = (CSS as unknown as { highlights?: HighlightRegistry }).highlights
  return reg && typeof (window as unknown as { Highlight?: unknown }).Highlight === 'function' ? reg : null
}

export function clearHighlights() {
  const reg = registry()
  reg?.delete('scribe-find')
  reg?.delete('scribe-find-current')
}

/** 지금 그려진 문장 안의 찾은 글자를 칠한다 (DOM을 바꾸지 않는 CSS Highlight) */
export function paintHighlights(container: HTMLElement | null, units: Unit[], hits: Hit[], current: number) {
  const reg = registry()
  if (!reg || !container) return
  const byUnit = new Map<string, { h: Hit; k: number }[]>()
  hits.forEach((h, k) => {
    const id = units[h.i]?.id
    if (!id) return
    const list = byUnit.get(id)
    if (list) list.push({ h, k })
    else byUnit.set(id, [{ h, k }])
  })
  const all: Range[] = []
  const cur: Range[] = []
  container.querySelectorAll<HTMLElement>('.utext[data-unit]').forEach((p) => {
    const list = byUnit.get(p.dataset.unit || '')
    if (!list) return
    const spans = p.querySelectorAll<HTMLElement>('[data-k="o"]')
    for (const { h, k } of list) {
      for (const sp of spans) {
        const o = Number(sp.dataset.o)
        const len = Number(sp.dataset.len)
        const a = Math.max(h.s, o)
        const b = Math.min(h.e, o + len)
        const node = sp.firstChild
        if (b <= a || !node || node.nodeType !== Node.TEXT_NODE) continue
        const r = new Range()
        r.setStart(node, a - o)
        r.setEnd(node, b - o)
        ;(k === current ? cur : all).push(r)
      }
    }
  })
  const H = (window as unknown as { Highlight: HighlightCtor }).Highlight
  reg.set('scribe-find', new H(...all))
  reg.set('scribe-find-current', new H(...cur))
}

interface Props {
  units: Unit[]
  initial: string
  onHits: (hits: Hit[], current: number) => void
  onJump: (hit: Hit) => void
  onClose: () => void
  inputRef: React.RefObject<HTMLInputElement | null>
}

/** 기록 안에서 찾기 (⌘F). 긴 기록은 보이는 부분만 그리므로 브라우저 찾기 대신 쓴다. */
export function TranscriptFind({ units, initial, onHits, onJump, onClose, inputRef }: Props) {
  const [q, setQ] = useState(initial)
  const [query, setQuery] = useState(initial)
  const [cur, setCur] = useState(0)
  const [showList, setShowList] = useState(false)

  useEffect(() => {
    const id = setTimeout(() => setQuery(q), 120)
    return () => clearTimeout(id)
  }, [q])

  const hits = useMemo(() => findHits(units, query), [units, query])

  // 새로 찾으면 지금 보이는 곳에서 가까운 첫 결과부터
  useEffect(() => {
    setCur(0)
    if (hits.length) onJump(hits[0])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

  useEffect(() => {
    onHits(hits, Math.min(cur, hits.length - 1))
  }, [hits, cur, onHits])

  const go = (d: number) => {
    if (!hits.length) return
    const next = (cur + d + hits.length) % hits.length
    setCur(next)
    onJump(hits[next])
  }

  return (
    <div className="find-anchor">
      <div className="find-bar" role="search">
        <input
          ref={inputRef}
          value={q}
          autoFocus
          placeholder="기록에서 찾기"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
              e.preventDefault()
              if (query !== q) setQuery(q)
              else go(e.shiftKey ? -1 : 1)
            }
            if (e.key === 'Escape') onClose()
          }}
        />
        <span className="find-count">{query ? (hits.length ? `${cur + 1}/${hits.length >= MAX_HITS ? `${MAX_HITS}+` : hits.length}` : '없음') : ''}</span>
        <button className="icon-btn" onClick={() => go(-1)} disabled={!hits.length} title="이전 (Shift+Enter)"><ChevronUp size={15} /></button>
        <button className="icon-btn" onClick={() => go(1)} disabled={!hits.length} title="다음 (Enter)"><ChevronDown size={15} /></button>
        <button className={`icon-btn ${showList ? 'on' : ''}`} onClick={() => setShowList((v) => !v)} disabled={!hits.length} title="결과 목록"><List size={15} /></button>
        <button className="icon-btn" onClick={onClose} title="닫기 (Esc)"><X size={15} /></button>
      </div>
      {showList && hits.length > 0 && (
        <div className="find-list">
          {hits.slice(0, 300).map((h, k) => {
            const text = units[h.i].text
            const a = Math.max(0, h.s - 18)
            const b = Math.min(text.length, h.e + 24)
            return (
              <button
                key={`${h.i}-${h.s}`}
                className={`find-item ${k === cur ? 'on' : ''}`}
                onClick={() => {
                  setCur(k)
                  onJump(h)
                }}
              >
                <span className="find-time">{fmtTime(units[h.i].t0)}</span>
                <span className="find-snippet">
                  {a > 0 && '…'}
                  {text.slice(a, h.s)}
                  <mark>{text.slice(h.s, h.e)}</mark>
                  {text.slice(h.e, b)}
                  {b < text.length && '…'}
                </span>
              </button>
            )
          })}
          {hits.length > 300 && <p className="muted pad">처음 300개만 보여요. 더 자세히 찾아 보세요.</p>}
        </div>
      )}
    </div>
  )
}

/** 찾기 창을 열고 닫는 상태와 ⌘F 가로채기 */
export function useFindShortcut(enabled: boolean) {
  const [open, setOpen] = useState(false)
  const [initial, setInitial] = useState('')
  const inputRef = useRef<HTMLInputElement | null>(null)
  useEffect(() => {
    if (!enabled) return
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey || e.key.toLowerCase() !== 'f') return
      const target = e.target as HTMLElement | null
      // 노트에서는 브라우저 찾기를 그대로 쓴다
      if (target?.closest?.('.notes-pane')) return
      // 찾기 창에서 한 번 더 누르면 브라우저 찾기
      if (open && document.activeElement === inputRef.current) return
      e.preventDefault()
      const sel = window.getSelection()?.toString().trim() || ''
      if (sel && sel.length < 60) setInitial(sel)
      setOpen(true)
      setTimeout(() => inputRef.current?.select(), 0)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [enabled, open])
  return { open, setOpen, initial, inputRef }
}
