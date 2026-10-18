import { useEffect, useRef, useState } from 'react'
import { COMMENT_TO_NOTES, usePref } from '../lib/prefs'
import { Floating } from './Floating'

interface Props {
  rect: DOMRect
  mode: 'comment' | 'memo' | 'divider'
  quote?: string
  initial?: string
  onSubmit: (text: string, toNotes: boolean) => Promise<void> | void
  onClose: () => void
}

const TITLES = { comment: '댓글', memo: '메모', divider: '구분선 넣기' }
const PLACEHOLDERS = { comment: '댓글을 남겨 주세요', memo: '이 시점에 남길 메모', divider: '이름 (예: 쉬는 시간, 2부) · 비워 두면 선만' }

export function Composer({ rect, mode, quote, initial, onSubmit, onClose }: Props) {
  const [text, setText] = useState(initial ?? '')
  const [busy, setBusy] = useState(false)
  const [toNotes, setToNotes] = usePref(COMMENT_TO_NOTES, false)
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    ref.current?.focus()
  }, [mode])

  const submit = async () => {
    const v = text.trim()
    if ((!v && mode !== 'divider') || busy) return
    setBusy(true)
    try {
      await onSubmit(v, (mode === 'comment' || mode === 'memo') && toNotes)
      onClose()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Floating rect={rect} onClose={onClose} className="composer">
      <div className="composer-head">{TITLES[mode]}</div>
      {quote && <blockquote className="composer-quote">{quote}</blockquote>}
      <textarea
        ref={ref}
        rows={mode === 'divider' ? 1 : 3}
        value={text}
        placeholder={PLACEHOLDERS[mode]}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
      />
      <div className="composer-foot">
        {mode === 'comment' || mode === 'memo' ? (
          <label className="to-notes" title="저장하면서 오른쪽 노트에도 추가해요. 다음에도 기억해요.">
            <input type="checkbox" checked={toNotes} onChange={(e) => setToNotes(e.target.checked)} />
            노트에도 추가
          </label>
        ) : (
          <span className="hint">Enter 저장 · Esc 닫기</span>
        )}
        <button className="btn btn-primary btn-sm" disabled={(!text.trim() && mode !== 'divider') || busy} onClick={submit}>
          {mode === 'divider' ? '넣기' : '저장'}
        </button>
      </div>
    </Floating>
  )
}
