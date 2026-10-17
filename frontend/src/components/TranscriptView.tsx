import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent as RMouseEvent, type RefObject } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Highlighter, MessageSquarePlus, NotebookPen, PenLine, SeparatorHorizontal, StickyNote } from 'lucide-react'
import { type Annotation, type Unit } from '../lib/api'
import { annActions } from '../lib/annActions'
import { buildModel, piecesFor, timeAtOffset, unitAtTime, type Piece, type RangeAnn, type View } from '../lib/model'
import { readSelection, type TextSelection } from '../lib/selection'
import { bridges, seekTo, useStore } from '../lib/store'
import { fmtTime } from '../lib/time'
import { sendToNotes } from '../lib/notesExport'
import { fmtClock, wallAt } from '../lib/clock'
import { usePref, usePrefStr, VIEW_CLOCK, VIEW_COMMENTS_COLLAPSED, VIEW_FONT, type FontSize } from '../lib/prefs'
import { AnnotationPopover } from './AnnotationPopover'
import { CommentThread } from './CommentThread'
import { DividerRow } from './DividerRow'
import { Composer } from './Composer'
import { Floating } from './Floating'
import { MemoCard } from './MemoCard'
import { SpeakerLabel } from './SpeakerLabel'
import { clearHighlights, paintHighlights, TranscriptFind, useFindShortcut, type Hit } from './TranscriptFind'

interface Props {
  view: View
  meetingId: string
  units: Unit[]
  annotations: Annotation[]
  partial?: { t0: number; t1: number; text: string } | null
  recording?: boolean
  /** 스크롤되는 영역. 긴 기록은 여기에 보이는 문장만 그린다. */
  scrollRef: RefObject<HTMLDivElement | null>
}

type ComposerState =
  | { mode: 'comment' | 'edit'; rect: DOMRect; start: { unit: string; offset: number }; end: { unit: string; offset: number }; quote: string }
  | { mode: 'memo' | 'divider'; rect: DOMRect; t: number; after: string | null }

function markClasses(anns: Annotation[]): string {
  const cls: string[] = []
  for (const a of anns) {
    if (a.kind === 'comment') cls.push('mk-comment', a.status === 'resolved' ? 'resolved' : '')
    if (a.kind === 'highlight') cls.push('mk-hl', `hl-${a.color || 'yellow'}`)
    if (a.pos && 'approx' in a.pos && a.pos.approx) cls.push('mk-approx')
  }
  return cls.filter(Boolean).join(' ')
}

function PieceView({ p, onOpen, hover }: { p: Piece; onOpen: (ids: string[], el: HTMLElement) => void; hover: string | null }) {
  const ids = p.type === 'edit' ? [p.ann.id, ...p.anns.map((a) => a.id)] : p.anns.map((a) => a.id)
  const hot = hover !== null && ids.includes(hover) ? ' hot' : ''
  const click = ids.length
    ? (e: RMouseEvent<HTMLElement>) => {
        if (window.getSelection()?.isCollapsed !== false) onOpen(ids, e.currentTarget)
      }
    : undefined
  if (p.type === 'text') {
    return (
      <span data-o={p.o} data-k="o" data-len={p.text.length} className={(markClasses(p.anns) + hot) || undefined} onClick={click}>
        {p.text}
      </span>
    )
  }
  const extra = markClasses(p.anns)
  const repl = p.ann.replacement || ''
  switch (p.display) {
    case 'proof':
      return (
        <span className={`mk-edit proof ${extra}`} onClick={click} title="회의 중 고친 내용이에요. 눌러서 원본에 적용할 수 있어요.">
          <del data-o={p.o} data-k="o" data-len={p.text.length}>{p.text}</del>
          <ins data-o={p.o} data-k="r" data-len={p.text.length}>{repl}</ins>
        </span>
      )
    case 'applied':
      return (
        <span className={`mk-edit applied ${extra}`} onClick={click} title={`원문: ${p.text}`}>
          <span data-o={p.o} data-k="r" data-len={p.text.length}>{repl}</span>
        </span>
      )
    case 'same':
      return (
        <span className={`mk-edit same ${extra}`} onClick={click} title="회의 중 고친 내용과 원본이 같아요">
          <span data-o={p.o} data-k="o" data-len={p.text.length}>{p.text}</span>
        </span>
      )
    case 'clean-applied':
      return <span data-o={p.o} data-k="r" data-len={p.text.length}>{repl}</span>
    default:
      return <span data-o={p.o} data-k="o" data-len={p.text.length} className={extra || undefined} onClick={extra ? click : undefined}>{p.text}</span>
  }
}

interface RowProps {
  u: Unit
  ranges: RangeAnn[] | undefined
  showSpeaker: boolean
  active: boolean
  flash: boolean
  comments: Annotation[] | undefined
  memos: Annotation[] | undefined
  dividers: Annotation[] | undefined
  view: View
  meetingId: string
  showMarks: boolean
  hover: string | null
  flashAnn: string | null
  onOpen: (ids: string[], el: HTMLElement) => void
  onMemo: (u: Unit, el: HTMLElement) => void
  onComment: (u: Unit, el: HTMLElement) => void
  onDivider: (u: Unit, el: HTMLElement) => void
  onHover: (id: string | null) => void
  timeLabel: (t: number) => string
  timeTitle: (t: number) => string
}

const UnitRow = memo(function UnitRow({ u, ranges, showSpeaker, active, flash, comments, memos, dividers, view, meetingId, showMarks, hover, flashAnn, onOpen, onMemo, onComment, onDivider, onHover, timeLabel, timeTitle }: RowProps) {
  const pieces = useMemo(() => piecesFor(u.text, ranges, view, showMarks), [u.text, ranges, view, showMarks])
  return (
    <>
      {showSpeaker && (
        <div className="speaker-row">
          <SpeakerLabel label={u.speaker ?? null} />
        </div>
      )}
      <div id={`unit-${u.id}`} className={`unit ${active ? 'active' : ''} ${flash ? 'flash' : ''}`}>
        <button className="u-time" onClick={() => seekTo(u.t0)} title={timeTitle(u.t0)}>
          {timeLabel(u.t0)}
        </button>
        <p className="utext" data-unit={u.id} data-len={u.text.length}>
          {pieces.map((p, k) => (
            <PieceView key={k} p={p} onOpen={onOpen} hover={hover} />
          ))}
        </p>
        <div className="u-actions">
          <button className="u-act" title="이 문장에 댓글" onClick={(e) => onComment(u, e.currentTarget)}>
            <MessageSquarePlus size={14} />
          </button>
          <button className="u-act" title="이 문장 뒤에 메모" onClick={(e) => onMemo(u, e.currentTarget)}>
            <StickyNote size={14} />
          </button>
          <button className="u-act" title="이 문장 뒤에 구분선 (쉬는 시간, 단락)" onClick={(e) => onDivider(u, e.currentTarget)}>
            <SeparatorHorizontal size={14} />
          </button>
        </div>
      </div>
      {showMarks && comments?.map((c) => <CommentThread key={c.id} ann={c} view={view} meetingId={meetingId} flash={flashAnn === c.id} onHover={onHover} />)}
      {showMarks && memos?.map((m) => <MemoCard key={m.id} ann={m} view={view} />)}
      {dividers?.map((d) => <DividerRow key={d.id} ann={d} view={view} />)}
    </>
  )
})

function unitHasAnn(model: ReturnType<typeof buildModel>, unitId: string, annId: string): boolean {
  return !!model.perUnit.get(unitId)?.some((r) => r.ann.id === annId)
}

/** 그리기 전 높이 추정 (그린 뒤에는 실제 높이를 잰다). 한 줄에 한글 약 42자. */
function estimateRow(u: Unit, speaker: boolean, model: ReturnType<typeof buildModel>, showMarks: boolean): number {
  let h = 10 + Math.max(1, Math.ceil(u.text.length / 42)) * 27
  if (speaker) h += 30
  if (showMarks) h += (model.commentEnds.get(u.id)?.length || 0) * 80 + (model.memosAfter.get(u.id)?.length || 0) * 64
  h += (model.dividersAfter.get(u.id)?.length || 0) * 44
  return h
}

export function TranscriptView({ view, meetingId, units, annotations, partial, recording, scrollRef }: Props) {
  const showMarks = useStore((s) => s.showMarks)
  const playing = useStore((s) => s.playing)
  const follow = useStore((s) => s.follow)
  const flashUnit = useStore((s) => s.flashUnit)
  const toast = useStore((s) => s.toast)
  const ref = useRef<HTMLDivElement>(null)
  const [sel, setSel] = useState<TextSelection | null>(null)
  const [composer, setComposer] = useState<ComposerState | null>(null)
  const [pop, setPop] = useState<{ ids: string[]; rect: DOMRect } | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const [flashAnn, setFlashAnn] = useState<string | null>(null)

  const [collapsed] = usePref(VIEW_COMMENTS_COLLAPSED, false)
  const [clockView] = usePref(VIEW_CLOCK, false)
  const [font] = usePrefStr<FontSize>(VIEW_FONT, 'm')
  const clock = useStore((s) => (s.meeting?.id === meetingId ? s.meeting.clock : undefined))
  // 시각에 마우스를 올리면 실제 시각, 보기 설정에서 켜면 실제 시각으로 표시
  const timeLabel = useCallback((t: number) => {
    const w = clockView ? wallAt(clock, t) : null
    return w !== null ? fmtClock(w) : fmtTime(t)
  }, [clock, clockView])
  const timeTitle = useCallback((t: number) => {
    const w = wallAt(clock, t)
    return w !== null ? `${clockView ? fmtTime(t) : fmtClock(w, true)} · 이 부분부터 듣기` : '이 부분부터 듣기'
  }, [clock, clockView])

  const model = useMemo(() => buildModel(units, annotations), [units, annotations])
  const byId = useMemo(() => new Map(annotations.map((a) => [a.id, a])), [annotations])
  const unitById = useMemo(() => new Map(units.map((u) => [u.id, u])), [units])
  const activeId = useStore((s) => (recording || (!s.playing && s.playerTime <= 0) ? null : unitAtTime(units, s.playerTime)))
  const indexById = useMemo(() => new Map(units.map((u, i) => [u.id, i])), [units])
  const listRef = useRef<HTMLDivElement>(null)
  const [scrollMargin, setScrollMargin] = useState(0)
  const showSpeakerAt = (i: number) => view === 'final' && (i === 0 || units[i - 1].speaker !== units[i].speaker)

  // 긴 기록: 보이는 문장(+앞뒤 여유)만 그린다
  const virtualizer = useVirtualizer({
    count: units.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => estimateRow(units[i], showSpeakerAt(i), model, showMarks),
    getItemKey: (i) => units[i].id,
    overscan: 10,
    scrollMargin,
  })

  // 목록 위의 안내 카드 높이만큼 목록이 아래에서 시작한다
  useLayoutEffect(() => {
    const top = listRef.current?.offsetTop ?? 0
    if (Math.abs(top - scrollMargin) > 1) setScrollMargin(top)
  })

  const scrollToIndex = useCallback(
    (i: number, align: 'center' | 'start' | 'end' = 'center') => virtualizer.scrollToIndex(i, { align }),
    [virtualizer],
  )

  useEffect(() => {
    bridges.transcript = {
      scrollToUnit: (id, opts) => {
        const i = indexById.get(id)
        if (i !== undefined) scrollToIndex(i, opts?.align ?? 'center')
      },
      // M 단축키: 지금 재생 위치의 문장 뒤에 메모
      memoAt: (t) => {
        const id = unitAtTime(units, t) ?? units[units.length - 1]?.id
        const i = id ? indexById.get(id) : undefined
        if (!id || i === undefined) return
        scrollToIndex(i)
        setTimeout(() => {
          const el = document.getElementById(`unit-${id}`)
          if (el) setComposer({ mode: 'memo', rect: el.getBoundingClientRect(), t, after: id })
        }, 120)
      },
    }
    return () => {
      bridges.transcript = null
    }
  }, [indexById, scrollToIndex, units])

  // 찾기 (⌘F)
  const find = useFindShortcut(true)
  const hitsRef = useRef<{ hits: Hit[]; current: number }>({ hits: [], current: -1 })
  const onHits = useCallback((hits: Hit[], current: number) => {
    hitsRef.current = { hits, current }
    paintHighlights(listRef.current, units, hits, current)
  }, [units])
  const onJump = useCallback((h: Hit) => scrollToIndex(h.i), [scrollToIndex])
  const closeFind = useCallback(() => {
    find.setOpen(false)
    hitsRef.current = { hits: [], current: -1 }
    clearHighlights()
  }, [find])
  // 스크롤해서 새로 그린 문장에도 칠한다
  useEffect(() => {
    if (find.open) paintHighlights(listRef.current, units, hitsRef.current.hits, hitsRef.current.current)
  })
  useEffect(() => clearHighlights, [])

  const captureSelection = useCallback(() => {
    setTimeout(() => {
      if (!ref.current) return
      setSel(readSelection(ref.current))
    }, 0)
  }, [])

  useEffect(() => {
    const onSel = () => {
      const s = window.getSelection()
      if (!s || s.isCollapsed) setSel(null)
    }
    document.addEventListener('selectionchange', onSel)
    return () => document.removeEventListener('selectionchange', onSel)
  }, [])

  // 재생 중인 문장 따라가기
  useEffect(() => {
    if (!activeId || !playing || !follow) return
    const i = indexById.get(activeId)
    if (i !== undefined) scrollToIndex(i)
  }, [activeId, playing, follow, indexById, scrollToIndex])

  useEffect(() => {
    if (!flashUnit) return
    const i = indexById.get(flashUnit)
    if (i !== undefined) scrollToIndex(i)
  }, [flashUnit, indexById, scrollToIndex])

  const create = async (body: Record<string, unknown>, toNotes = false) => {
    try {
      const ann = await annActions.add(meetingId, { view, ...body })
      if (toNotes) await sendToNotes(meetingId, [ann], view)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const act = async (kind: 'comment' | 'edit' | 'highlight' | 'note') => {
    if (!sel) return
    const s = sel
    window.getSelection()?.removeAllRanges()
    setSel(null)
    if (kind === 'highlight') {
      await create({ kind: 'highlight', start: s.start, end: s.end, color: 'yellow' })
    } else if (kind === 'note') {
      const u = unitById.get(s.start.unit)
      const t = u ? timeAtOffset(u, s.start.offset) : 0
      if (bridges.notes) {
        bridges.notes.insertQuote(s.text, t)
        toast('노트에 인용으로 넣었어요', 'success')
      }
    } else {
      setComposer({ mode: kind, rect: s.rect, start: s.start, end: s.end, quote: s.text })
    }
  }

  const openPop = useCallback(
    (ids: string[], el: HTMLElement) => {
      const comment = ids.map((id) => byId.get(id)).find((a) => a?.kind === 'comment')
      if (comment) {
        // 댓글은 문장 아래에 보이므로 그쪽으로 이동해 깜빡인다
        document.getElementById(`cmt-${comment.id}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
        setFlashAnn(comment.id)
        setTimeout(() => setFlashAnn((v) => (v === comment.id ? null : v)), 1400)
        return
      }
      setPop({ ids, rect: el.getBoundingClientRect() })
    },
    [byId],
  )
  const openMemo = useCallback((u: Unit, el: HTMLElement) => setComposer({ mode: 'memo', rect: el.getBoundingClientRect(), t: u.t1 + 0.01, after: u.id }), [])
  const openDivider = useCallback((u: Unit, el: HTMLElement) => setComposer({ mode: 'divider', rect: el.getBoundingClientRect(), t: u.t1 + 0.01, after: u.id }), [])
  const openComment = useCallback(
    (u: Unit, el: HTMLElement) =>
      setComposer({ mode: 'comment', rect: el.getBoundingClientRect(), start: { unit: u.id, offset: 0 }, end: { unit: u.id, offset: u.text.length }, quote: u.text }),
    [],
  )

  // 글자를 선택한 상태에서 C 또는 ⌘⌥M → 댓글
  useEffect(() => {
    if (!sel || composer) return
    const onKey = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase()
      const plainC = k === 'c' && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey
      const docsShortcut = (e.metaKey || e.ctrlKey) && e.altKey && (k === 'm' || e.code === 'KeyM')
      if (plainC || docsShortcut) {
        e.preventDefault()
        act('comment')
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  })

  const popAnns = pop ? pop.ids.map((id) => byId.get(id)).filter((a): a is Annotation => !!a) : []

  return (
    <div className={`transcript view-${view} font-${font} ${showMarks ? '' : 'marks-off'} ${collapsed ? 'cmts-collapsed' : ''}`} ref={ref} onMouseUp={captureSelection} onKeyUp={(e) => e.shiftKey && captureSelection()}>
      {find.open && (
        <TranscriptFind units={units} initial={find.initial} inputRef={find.inputRef} onHits={onHits} onJump={onJump} onClose={closeFind} />
      )}
      {showMarks && model.memosBefore.map((m) => <MemoCard key={m.id} ann={m} view={view} />)}
      {model.dividersBefore.map((d) => <DividerRow key={d.id} ann={d} view={view} />)}
      <div ref={listRef} className="vlist" style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map((vi) => {
          const i = vi.index
          const u = units[i]
          return (
            <div
              key={vi.key}
              data-index={i}
              ref={virtualizer.measureElement}
              className="vrow"
              style={{ transform: `translateY(${vi.start - scrollMargin}px)` }}
            >
              <UnitRow
                u={u}
                ranges={model.perUnit.get(u.id)}
                showSpeaker={showSpeakerAt(i)}
                active={u.id === activeId}
                flash={u.id === flashUnit}
                comments={model.commentEnds.get(u.id)}
                memos={model.memosAfter.get(u.id)}
                dividers={model.dividersAfter.get(u.id)}
                view={view}
                meetingId={meetingId}
                showMarks={showMarks}
                hover={hover !== null && unitHasAnn(model, u.id, hover) ? hover : null}
                flashAnn={flashAnn !== null && model.commentEnds.get(u.id)?.some((c) => c.id === flashAnn) ? flashAnn : null}
                onOpen={openPop}
                onMemo={openMemo}
                onComment={openComment}
                onDivider={openDivider}
                onHover={setHover}
                timeLabel={timeLabel}
                timeTitle={timeTitle}
              />
            </div>
          )
        })}
      </div>
      {partial && partial.text && (
        <div className="unit partial">
          <span className="u-time">{timeLabel(partial.t0)}</span>
          <p className="utext-partial">
            {partial.text}
            <span className="typing" />
          </p>
        </div>
      )}

      {sel && !composer && (
        <Floating rect={sel.rect} placement="above" className="sel-toolbar">
          <button className="primary" onMouseDown={(e) => e.preventDefault()} onClick={() => act('comment')} title="댓글 (C)"><MessageSquarePlus size={15} />댓글<kbd>C</kbd></button>
          <button onMouseDown={(e) => e.preventDefault()} onClick={() => act('highlight')}><Highlighter size={15} />강조</button>
          <button onMouseDown={(e) => e.preventDefault()} onClick={() => act('edit')}><PenLine size={15} />수정</button>
          <span className="sep" />
          <button onMouseDown={(e) => e.preventDefault()} onClick={() => act('note')}><NotebookPen size={15} />노트로</button>
        </Floating>
      )}

      {composer && (composer.mode === 'comment' || composer.mode === 'edit') && (
        <Composer
          rect={composer.rect}
          mode={composer.mode}
          quote={composer.quote}
          initial={composer.mode === 'edit' ? composer.quote : ''}
          onClose={() => setComposer(null)}
          onSubmit={(text, toNotes) =>
            create(
              composer.mode === 'comment'
                ? { kind: 'comment', start: composer.start, end: composer.end, body: text }
                : { kind: 'edit', start: composer.start, end: composer.end, replacement: text },
              toNotes,
            )
          }
        />
      )}
      {composer && (composer.mode === 'memo' || composer.mode === 'divider') && (
        <Composer
          rect={composer.rect}
          mode={composer.mode}
          onClose={() => setComposer(null)}
          onSubmit={(text, toNotes) => create({ kind: composer.mode, t: composer.t, after: composer.after, body: text }, toNotes)}
        />
      )}
      {pop && popAnns.length > 0 && <AnnotationPopover anns={popAnns} rect={pop.rect} view={view} onClose={() => setPop(null)} />}
    </div>
  )
}
