import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { Copy, Bot } from 'lucide-react'
import { useMemo } from 'react'
import { api, type Meeting } from '../lib/api'
import { useStore } from '../lib/store'
import { fmtRelative } from '../lib/time'

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    useStore.getState().toast('복사했어요', 'success')
  } catch {
    useStore.getState().toast('복사하지 못했어요', 'error')
  }
}

export function SummaryView({ meeting }: { meeting: Meeting }) {
  const html = useMemo(() => (meeting.summary_md ? DOMPurify.sanitize(marked.parse(meeting.summary_md, { async: false }) as string) : ''), [meeting.summary_md])

  if (!meeting.summary_md) {
    return (
      <div className="summary-empty">
        <Bot size={26} />
        <h3>AI 요약은 내가 구독한 Claude가 만들어요</h3>
        <p className="muted">
          이 앱은 MCP로 Claude Desktop이나 Claude Code와 연결됩니다. Claude에게 아래처럼 말하면 노트·첨삭·기록을 읽고 회의록을 만들어 여기에 저장해요.
        </p>
        <div className="prompt-box">
          <code>방금 회의 회의록으로 정리해줘</code>
          <button className="icon-btn" title="복사" onClick={() => copy('방금 회의 회의록으로 정리해줘')}><Copy size={14} /></button>
        </div>
        <div className="summary-actions">
          <button className="btn btn-sm" onClick={() => copy(meeting.id)}><Copy size={14} />회의 ID 복사</button>
          <button className="btn btn-sm" onClick={async () => copy(await api.exportMd(meeting.id))}><Copy size={14} />전체 내용을 Markdown으로 복사</button>
        </div>
        <p className="hint">MCP 연결 방법은 왼쪽 아래 설정에 있어요.</p>
      </div>
    )
  }
  return (
    <div className="summary">
      <div className="summary-meta">
        <Bot size={14} />
        Claude가 저장한 요약 · {meeting.summary_updated_at ? fmtRelative(meeting.summary_updated_at) : ''}
        <span className="spacer" />
        <button className="btn btn-sm btn-ghost" onClick={() => copy(meeting.summary_md || '')}><Copy size={14} />복사</button>
      </div>
      <article className="prose" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  )
}
