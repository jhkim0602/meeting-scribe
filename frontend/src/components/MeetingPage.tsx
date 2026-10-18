import { lazy, Suspense } from 'react'
import { Group, Panel, Separator } from 'react-resizable-panels'
import { useStore } from '../lib/store'
import { Dock } from './Dock'
import { PageHeader } from './PageHeader'
import { TranscriptPane } from './TranscriptPane'

// 노트 편집기(BlockNote)는 크므로 따로 불러온다: 기록이 먼저 뜨고 노트는 바로 뒤따른다
const NotesPane = lazy(() => import('./NotesPane').then((m) => ({ default: m.NotesPane })))

export function MeetingPage() {
  const meeting = useStore((s) => s.meeting)
  if (!meeting) return <div className="page-loading" />
  return (
    <div className="meeting-page">
      <PageHeader />
      <div className="split">
        <Group orientation="horizontal" id="split-v1">
          <Panel id="transcript" defaultSize="58%" minSize={420}>
            <TranscriptPane />
          </Panel>
          <Separator className="resize-handle" />
          <Panel id="notes" defaultSize="42%" minSize={320}>
            <Suspense fallback={<section className="pane notes-pane" />}>
              <NotesPane />
            </Suspense>
          </Panel>
        </Group>
      </div>
      <Dock />
    </div>
  )
}
