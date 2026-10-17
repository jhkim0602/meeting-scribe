import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent as RMouseEvent, type RefObject } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Highlighter, MessageSquarePlus, NotebookPen, PenLine, SeparatorHorizontal, StickyNote, UserRound } from 'lucide-react'
import { api, type Annotation, type Participant, type Unit } from '../lib/api'
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
import { SpeakerPicker } from './SpeakerPicker'
import { addParticipant, assignSpeaker, participantIndex } from '../lib/speakers'
import { buildTurns, endsSentence, type Turn } from '../lib/turns'
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

function UnitText({ u, ranges, view, showMarks, active, flash, hover, onOpen, title }: {
  u: Unit
  ranges: RangeAnn[] | undefined
  view: View
  showMarks: boolean
  active: boolean
  flash: boolean
  hover: string | null
  onOpen: (ids: string[], el: HTMLElement) => void
  title: string
}) {
  const pieces = useMemo(() => piecesFor(u.text, ranges, view, showMarks), [u.text, ranges, view, showMarks])
  return (
    <span id={`unit-${u.id}`} className={`utext${active ? ' active' : ''}${flash ? ' flash' : ''}`} data-unit={u.id} data-len={u.text.length} data-t={title}>
      {pieces.map((p, k) => (
        <PieceView key={k} p={p} onOpen={onOpen} hover={hover} />
      ))}
    </span>
  )
}

/** 실시간 단락의 화자: 직접 고른 것은 진하게, 목소리로 추정한 것은 흐리게 */
function SpeakerChip({ turn, participants, onPick }: { turn: Turn; participants: Participant[]; onPick: (rect: DOMRect) => void }) {
  const u = turn.units[0]
  const pid = turn.key?.startsWith('p:') ? turn.key.slice(2) : null
  const person = pid ? participants.find((p) => p.id === pid) : undefined
  const manual = turn.units.some((x) => x.speaker_src === 'manual')
  const name = person ? person.name : u.speaker_auto ? `화자 ${u.speaker_auto}` : '화자?'
  const conf = Math.round((u.speaker_conf ?? 0) * 100)
  const title = manual
    ? '직접 고른 화자 · 눌러서 바꾸기'
    : person
      ? `목소리로 추정했어요${conf ? ` (${conf}%)` : ''} · 눌러서 확정하거나 바꾸기`
      : '아직 누군지 몰라요 · 눌러서 고르면 같은 목소리 문장에 모두 반영돼요'
  return (
    <button
      className={`spk-chip sp-${person ? participantIndex(participants, pid) : 0} ${manual ? 'manual' : person ? 'guess' : 'unknown'}`}
      onClick={(e) => onPick(e.currentTarget.getBoundingClientRect())}
      title={title}
    >
      <span className="sp-dot" />
      {name}
      {!manual && person && <span className="spk-q">?</span>}
    </button>
  )
}

interface TurnProps {
  turn: Turn
  model: ReturnType<typeof buildModel>
  view: View
  meetingId: string
  showMarks: boolean
  showSpeaker: boolean
  participants: Participant[]
  liveSpeakers: boolean
  activeId: string | null
  flashUnit: string | null
  hover: string | null
  flashAnn: string | null
  partial: { t0: number; text: string } | null
  onOpen: (ids: string[], el: HTMLElement) => void
  onMemo: (u: Unit, el: HTMLElement) => void
  onComment: (u: Unit, el: HTMLElement) => void
  onDivider: (u: Unit, el: HTMLElement) => void
  onHover: (id: string | null) => void
  onHoverUnit: (id: string | null) => void
  onPickSpeaker: (unitIds: string[], rect: DOMRect, one: boolean) => void
  timeLabel: (t: number) => string
  timeTitle: (t: number) => string
}

/** 화자 단락: 같은 사람이 이어서 말한 문장들을 한 문단으로 보여 준다. 문장마다 따로 고르고 댓글을 달 수 있다. */
const TurnRow = memo(function TurnRow(p: TurnProps) {
  const { turn, model, view } = p
  const body = useRef<HTMLDivElement>(null)
  const [hu, setHu] = useState<{ id: string; top: number } | null>(null)
  const first = turn.units[0]
  const onOver = (e: RMouseEvent) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>('.utext')
    if (!el || !body.current || el.dataset.unit === hu?.id) return
    setHu({ id: el.dataset.unit!, top: Math.max(0, el.getBoundingClientRect().top - body.current.getBoundingClientRect().top - 2) })
    p.onHoverUnit(el.dataset.unit!)
  }
  const target = (hu && turn.units.find((u) => u.id === hu.id)) || null
  const comments = turn.units.flatMap((u) => model.commentEnds.get(u.id) || [])
  const memos = turn.units.flatMap((u) => model.memosAfter.get(u.id) || [])
  const dividers = turn.units.flatMap((u) => model.dividersAfter.get(u.id) || [])
  return (
    <div className="turn" id={`turn-${turn.id}`}>
      {p.showSpeaker && (
        <div className="turn-speaker">
          {view === 'final' ? (
            <SpeakerLabel label={first.speaker ?? null} />
          ) : (
            <SpeakerChip turn={turn} participants={p.participants} onPick={(rect) => p.onPickSpeaker(turn.units.map((u) => u.id), rect, false)} />
          )}
        </div>
      )}
      <div className="turn-body" ref={body} onMouseOver={onOver} onMouseLeave={() => { setHu(null); p.onHoverUnit(null) }}>
        <button className="u-time" onClick={() => seekTo(first.t0)} title={p.timeTitle(first.t0)}>
          {p.timeLabel(first.t0)}
        </button>
        <p className="turn-text">
          {turn.units.map((u, k) => (
            <span key={u.id}>
              {k > 0 && ' '}
              <UnitText
                u={u}
                ranges={model.perUnit.get(u.id)}
                view={view}
                showMarks={p.showMarks}
                active={u.id === p.activeId}
                flash={u.id === p.flashUnit}
                hover={p.hover}
                onOpen={p.onOpen}
                title={p.timeTitle(u.t0)}
              />
            </span>
          ))}
          {p.partial && (
            <span className="utext-partial">
              {' '}{p.partial.text}
              <span className="typing" />
            </span>
          )}
        </p>
        {target && (
          <div className="u-actions" style={{ top: hu!.top }}>
            <button className="u-act" title="이 문장에 댓글" onClick={(e) => p.onComment(target, e.currentTarget)}>
              <MessageSquarePlus size={14} />
            </button>
            <button className="u-act" title="이 문장 뒤에 메모" onClick={(e) => p.onMemo(target, e.currentTarget)}>
              <StickyNote size={14} />
            </button>
            <button className="u-act" title="이 문장 뒤에 구분선 (쉬는 시간, 단락)" onClick={(e) => p.onDivider(target, e.currentTarget)}>
              <SeparatorHorizontal size={14} />
            </button>
            {p.liveSpeakers && (
              <button className="u-act" title="이 문장만 화자 고르기 (Shift+숫자)" onClick={(e) => p.onPickSpeaker([target.id], e.currentTarget.getBoundingClientRect(), true)}>
                <UserRound size={14} />
              </button>
            )}
          </div>
        )}
      </div>
      {p.showMarks && comments.map((c) => <CommentThread key={c.id} ann={c} view={view} meetingId={p.meetingId} flash={p.flashAnn === c.id} onHover={p.onHover} />)}
      {p.showMarks && memos.map((m) => <MemoCard key={m.id} ann={m} view={view} />)}
      {dividers.map((d) => <DividerRow key={d.id} ann={d} view={view} />)}
    </div>
  )
})

function turnHasAnn(model: ReturnType<typeof buildModel>, turn: Turn, annId: string): boolean {
  return turn.units.some((u) => model.perUnit.get(u.id)?.some((r) => r.ann.id === annId))
}

/** 그리기 전 높이 추정 (그린 뒤에는 실제 높이를 잰다). 한 줄에 한글 약 42자. */
function estimateTurn(turn: Turn, speaker: boolean, model: ReturnType<typeof buildModel>, showMarks: boolean): number {
  const chars = turn.units.reduce((n, u) => n + u.text.length + 1, 0)
  let h = 10 + Math.max(1, Math.ceil(chars / 42)) * 27
  if (speaker) h += 30
  for (const u of turn.units) {
    if (showMarks) h += (model.commentEnds.get(u.id)?.length || 0) * 80 + (model.memosAfter.get(u.id)?.length || 0) * 64
    h += (model.dividersAfter.get(u.id)?.length || 0) * 44
  }
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
  const participants = useStore((s) => (s.meeting?.id === meetingId ? s.meeting.participants || [] : []))
  // 실시간 임시 화자를 쓰는지: 참여자를 넣었거나 목소리로 묶인 문장이 있으면
  const liveSpeakers = view === 'live' && (participants.length > 0 || units.some((u) => u.speaker_auto || u.speaker))
  // 같은 사람이 이어서 말한 문장은 한 단락으로 (구분선·메모가 있으면 끊는다)
  const turns = useMemo(() => {
    const breaks = new Set<string>([...model.memosAfter.keys(), ...model.dividersAfter.keys()])
    const last = units[units.length - 1]
    const pending = recording && liveSpeakers && last && !last.speaker && !last.speaker_auto ? last.id : null
    return buildTurns(units, view, breaks, pending)
  }, [units, view, model, recording, liveSpeakers])
  const turnOf = useMemo(() => {
    const m = new Map<string, number>()
    turns.forEach((t, i) => t.units.forEach((u) => m.set(u.id, i)))
    return m
  }, [turns])
  const listRef = useRef<HTMLDivElement>(null)
  const [scrollMargin, setScrollMargin] = useState(0)
  const showSpeakerAt = (i: number) => {
    const t = turns[i]
    const prev = i > 0 ? turns[i - 1] : null
    if (view === 'final') return !prev || prev.units[prev.units.length - 1].speaker !== t.units[0].speaker
    if (!liveSpeakers) return false
    if (t.key === null && !participants.length) return false
    return !prev || prev.key !== t.key
  }

  // 긴 기록: 보이는 단락(+앞뒤 여유)만 그린다
  const virtualizer = useVirtualizer({
    count: turns.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => estimateTurn(turns[i], showSpeakerAt(i), model, showMarks),
    getItemKey: (i) => turns[i].id,
    overscan: 8,
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
        const i = turnOf.get(id)
        if (i !== undefined) scrollToIndex(i, opts?.align ?? 'center')
      },
      // M 단축키: 지금 재생 위치의 문장 뒤에 메모
      memoAt: (t) => {
        const id = unitAtTime(units, t) ?? units[units.length - 1]?.id
        const i = id ? turnOf.get(id) : undefined
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
  }, [turnOf, scrollToIndex, units])

  // 찾기 (⌘F)
  const find = useFindShortcut(true)
  const hitsRef = useRef<{ hits: Hit[]; current: number }>({ hits: [], current: -1 })
  const onHits = useCallback((hits: Hit[], current: number) => {
    hitsRef.current = { hits, current }
    paintHighlights(listRef.current, units, hits, current)
  }, [units])
  const onJump = useCallback((h: Hit) => scrollToIndex(turnOf.get(units[h.i]?.id) ?? 0), [scrollToIndex, turnOf, units])
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
    const i = turnOf.get(activeId)
    if (i !== undefined) scrollToIndex(i)
  }, [activeId, playing, follow, turnOf, scrollToIndex])

  useEffect(() => {
    if (!flashUnit) return
    const i = turnOf.get(flashUnit)
    if (i !== undefined) scrollToIndex(i)
  }, [flashUnit, turnOf, scrollToIndex])

  // 화자 고르기: 단락의 화자 칩, 문장 위 버튼, 숫자키
  const [picker, setPicker] = useState<{ rect: DOMRect; unitIds: string[]; one: boolean } | null>(null)
  const hoverUnit = useRef<string | null>(null)
  const onHoverUnit = useCallback((id: string | null) => {
    hoverUnit.current = id
  }, [])
  const onPickSpeaker = useCallback((unitIds: string[], rect: DOMRect, one: boolean) => setPicker({ rect, unitIds, one }), [])
  useEffect(() => {
    if (!participants.length) return
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing || picker || composer) return
      if (t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))) return
      const n = /^Digit([1-9])$/.exec(e.code)
      const person = n ? participants[Number(n[1]) - 1] : undefined
      if (!person) return
      // 마우스를 올린 문장, 없으면 녹음 중의 마지막 문장
      const uid = hoverUnit.current ?? (recording ? units[units.length - 1]?.id : null)
      if (!uid) return
      e.preventDefault()
      if (view === 'final') {
        const label = units.find((u) => u.id === uid)?.speaker
        const st = useStore.getState()
        if (!label || !st.meeting) return
        api.patchMeeting(meetingId, { speakers: { ...st.meeting.speakers, [label]: person.name } }).then(() => Promise.all([st.refreshMeeting(), st.refreshFinal()]))
        return
      }
      const turn = turns[turnOf.get(uid) ?? -1]
      if (e.shiftKey || !turn) assignSpeaker(meetingId, [uid], person.id, 'one')
      else assignSpeaker(meetingId, turn.units.map((u) => u.id), person.id)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [participants, picker, composer, recording, units, turns, turnOf, view, meetingId])

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
          const turn = turns[i]
          const isLast = i === turns.length - 1
          const lastUnit = turn.units[turn.units.length - 1]
          // 말끝이 아닌데 이어서 말하는 중이면 진행 중 글자를 같은 단락에 잇는다
          const attach = isLast && partial?.text && partial.t0 - lastUnit.t1 <= 1.6 && !endsSentence(lastUnit.text) ? partial : null
          const activeHere = activeId !== null && turnOf.get(activeId) === i ? activeId : null
          const flashHere = flashUnit !== null && turnOf.get(flashUnit) === i ? flashUnit : null
          const commentsHere = turn.units.flatMap((u) => model.commentEnds.get(u.id) || [])
          return (
            <div
              key={vi.key}
              data-index={i}
              ref={virtualizer.measureElement}
              className="vrow"
              style={{ transform: `translateY(${vi.start - scrollMargin}px)` }}
            >
              <TurnRow
                turn={turn}
                model={model}
                view={view}
                meetingId={meetingId}
                showMarks={showMarks}
                showSpeaker={showSpeakerAt(i)}
                participants={participants}
                liveSpeakers={liveSpeakers && participants.length > 0}
                activeId={activeHere}
                flashUnit={flashHere}
                hover={hover !== null && turnHasAnn(model, turn, hover) ? hover : null}
                flashAnn={flashAnn !== null && commentsHere.some((c) => c.id === flashAnn) ? flashAnn : null}
                partial={attach ? { t0: attach.t0, text: attach.text } : null}
                onOpen={openPop}
                onMemo={openMemo}
                onComment={openComment}
                onDivider={openDivider}
                onHover={setHover}
                onHoverUnit={onHoverUnit}
                onPickSpeaker={onPickSpeaker}
                timeLabel={timeLabel}
                timeTitle={timeTitle}
              />
            </div>
          )
        })}
      </div>
      {partial && partial.text && !(turns.length && partial.t0 - turns[turns.length - 1].units[turns[turns.length - 1].units.length - 1].t1 <= 1.6 && !endsSentence(turns[turns.length - 1].units[turns[turns.length - 1].units.length - 1].text)) && (
        <div className="turn partial">
          <div className="turn-body">
            <span className="u-time">{timeLabel(partial.t0)}</span>
            <p className="turn-text utext-partial">
              {partial.text}
              <span className="typing" />
            </p>
          </div>
        </div>
      )}
      {picker && (
        <SpeakerPicker
          rect={picker.rect}
          mode="live"
          single={picker.one}
          participants={participants}
          current={picker.unitIds.length ? units.find((u) => u.id === picker.unitIds[0])?.speaker ?? null : null}
          onPick={(pid) => {
            const pk = picker
            setPicker(null)
            assignSpeaker(meetingId, pk.unitIds, pid, pk.one ? 'one' : undefined)
          }}
          onAdd={(name) => addParticipant(meetingId, name)}
          onClose={() => setPicker(null)}
        />
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
