import { useEffect, useRef, useState } from 'react'
import { Floating } from './Floating'

interface Props {
  rect: DOMRect
  mode: 'comment' | 'edit' | 'memo'
  quote?: string
  initial?: string
  onSubmit: (text: string) => Promise<void> | void
  onClose: () => void
}

const TITLES = { comment: '댓글', edit: '수정', memo: '메모' }
const PLACEHOLDERS = { comment: '댓글을 남겨 주세요', edit: '바르게 고친 문장', memo: '이 시점에 남길 메모' }

export function Composer({ rect, mode, quote, initial, onSubmit, onClose }: Props) {
  const [text, setText] = useState(initial ?? '')
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    ref.current?.focus()
    if (mode === 'edit') ref.current?.select()
  }, [mode])

  const submit = async () => {
    const v = text.trim()
    if (!v || busy) return
    if (mode === 'edit' && v === (quote || '').trim()) {
      onClose()
      return
    }
    setBusy(true)
    try {
      await onSubmit(v)
      onClose()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Floating rect={rect} onClose={onClose} className="composer">
      <div className="composer-head">{TITLES[mode]}</div>
      {quote && mode !== 'edit' && <blockquote className="composer-quote">{quote}</blockquote>}
      {mode === 'edit' && quote && <div className="composer-orig"><span>원문</span>{quote}</div>}
      <textarea
        ref={ref}
        rows={mode === 'edit' ? 2 : 3}
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
        <span className="hint">Enter 저장 · Shift+Enter 줄바꿈 · Esc 닫기</span>
        <button className="btn btn-primary btn-sm" disabled={!text.trim() || busy} onClick={submit}>
          저장
        </button>
      </div>
    </Floating>
  )
}
