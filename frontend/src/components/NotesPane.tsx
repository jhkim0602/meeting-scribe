import { BlockNoteSchema, defaultInlineContentSpecs, filterSuggestionItems, type PartialBlock } from '@blocknote/core'
import { ko } from '@blocknote/core/locales'
import { BlockNoteView } from '@blocknote/mantine'
import { createReactInlineContentSpec, getDefaultReactSlashMenuItems, SuggestionMenuController, useCreateBlockNote } from '@blocknote/react'
import { Check, Clock, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { bridges, currentClock, seekTo, useStore } from '../lib/store'
import { fmtTime } from '../lib/time'

const Timestamp = createReactInlineContentSpec(
  { type: 'timestamp', propSchema: { t: { default: 0 } }, content: 'none' },
  {
    render: ({ inlineContent }) => (
      <span
        className="ts-chip"
        contentEditable={false}
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => seekTo(Number(inlineContent.props.t))}
        title="이 시점으로 이동"
      >
        {fmtTime(Number(inlineContent.props.t))}
      </span>
    ),
    toExternalHTML: ({ inlineContent }) => <span>[{fmtTime(Number(inlineContent.props.t))}]</span>,
  },
)

const schema = BlockNoteSchema.create({
  inlineContentSpecs: { ...defaultInlineContentSpecs, timestamp: Timestamp },
})

type Editor = typeof schema.BlockNoteEditor

function useResolvedTheme(): 'light' | 'dark' {
  const theme = useStore((s) => s.theme)
  const [sys, setSys] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const on = () => setSys(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return theme === 'system' ? (sys ? 'dark' : 'light') : theme
}

function NotesEditor({ meetingId, initial }: { meetingId: string; initial: PartialBlock[] | undefined }) {
  const theme = useResolvedTheme()
  const [state, setState] = useState<'saved' | 'saving' | 'dirty'>('saved')
  const timer = useRef<number | null>(null)
  const editor = useCreateBlockNote(
    {
      schema,
      dictionary: { ...ko, placeholders: { ...ko.placeholders, emptyDocument: '여기에 자유롭게 필기하세요. / 를 누르면 제목, 체크리스트, 타임스탬프 등을 넣을 수 있어요.' } },
      initialContent: initial && initial.length ? (initial as never) : undefined,
    },
    [meetingId],
  ) as unknown as Editor

  const save = async () => {
    setState('saving')
    try {
      await api.saveNotes(meetingId, editor.document as unknown[])
      setState('saved')
    } catch {
      setState('dirty')
    }
  }

  useEffect(() => {
    // 마지막 빈 줄은 이어 쓰기용으로 남겨 두고 그 앞에 넣는다
    const insertAtEnd = (blocks: unknown[]) => {
      const doc = editor.document
      const last = doc[doc.length - 1]
      const lastEmpty = last.type === 'paragraph' && Array.isArray(last.content) && last.content.length === 0
      if (lastEmpty) editor.insertBlocks(blocks as never, last, 'before')
      else editor.insertBlocks([...blocks, { type: 'paragraph' }] as never, last, 'after')
      requestAnimationFrame(() => {
        const el = document.querySelector('.notes-editor')
        if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
      })
    }
    bridges.notes = {
      insertTimestamp: (t: number) => {
        editor.focus()
        editor.insertInlineContent([{ type: 'timestamp', props: { t: Math.round(t * 10) / 10 } } as never, ' '])
      },
      insertBlocks: (blocks: unknown[]) => insertAtEnd(blocks),
      insertQuote: (text: string, t: number) =>
        insertAtEnd([{ type: 'quote', content: [{ type: 'timestamp', props: { t: Math.round(t * 10) / 10 } }, ` ${text}`] }]),
    }
    return () => {
      bridges.notes = null
    }
  }, [editor])

  useEffect(
    () => () => {
      if (timer.current) {
        window.clearTimeout(timer.current)
        api.saveNotes(meetingId, editor.document as unknown[]).catch(() => undefined)
      }
    },
    [editor, meetingId],
  )

  return (
    <>
      <div className="notes-toolbar">
        <button className="tool-btn" onClick={() => bridges.notes?.insertTimestamp(currentClock())} title="지금 시각을 노트에 넣기 (클릭하면 그 시점으로 이동)">
          <Clock size={14} />
          <span>시각 넣기</span>
        </button>
        <span className="spacer" />
        <span className={`save-state ${state}`}>
          {state === 'saving' ? <Loader2 size={12} className="spin" /> : state === 'saved' ? <Check size={12} /> : null}
          {state === 'saving' ? '저장 중' : state === 'saved' ? '저장됨' : '저장 대기'}
        </span>
      </div>
      <div className="notes-editor">
        <BlockNoteView
          editor={editor}
          theme={theme}
          slashMenu={false}
          onChange={() => {
            setState('dirty')
            if (timer.current) window.clearTimeout(timer.current)
            timer.current = window.setTimeout(() => {
              timer.current = null
              save()
            }, 700)
          }}
        >
          <SuggestionMenuController
            triggerCharacter="/"
            getItems={async (query) =>
              filterSuggestionItems(
                [
                  {
                    title: '타임스탬프',
                    subtext: '지금 회의 시각을 넣어요. 누르면 그 시점으로 이동해요.',
                    aliases: ['time', 'timestamp', '시간', '시각'],
                    group: '회의',
                    icon: <Clock size={18} />,
                    onItemClick: () => bridges.notes?.insertTimestamp(currentClock()),
                  },
                  ...getDefaultReactSlashMenuItems(editor as never),
                ],
                query,
              )
            }
          />
        </BlockNoteView>
      </div>
    </>
  )
}

export function NotesPane() {
  const meetingId = useStore((s) => s.selectedId)!
  const [initial, setInitial] = useState<{ id: string; blocks: PartialBlock[] | undefined } | null>(null)

  useEffect(() => {
    let alive = true
    setInitial(null)
    api.notes(meetingId).then((n) => {
      if (alive) setInitial({ id: meetingId, blocks: (n.blocks as PartialBlock[] | null) || undefined })
    })
    return () => {
      alive = false
    }
  }, [meetingId])

  return (
    <section className="pane notes-pane">
      <header className="pane-head">
        <span className="pane-title">노트</span>
      </header>
      {initial && initial.id === meetingId ? <NotesEditor key={meetingId} meetingId={meetingId} initial={initial.blocks} /> : <div className="pane-loading"><Loader2 size={16} className="spin" /></div>}
    </section>
  )
}
