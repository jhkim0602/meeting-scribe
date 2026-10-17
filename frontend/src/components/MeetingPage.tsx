import { Group, Panel, Separator } from 'react-resizable-panels'
import { useStore } from '../lib/store'
import { Dock } from './Dock'
import { NotesPane } from './NotesPane'
import { PageHeader } from './PageHeader'
import { TranscriptPane } from './TranscriptPane'

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
            <NotesPane />
          </Panel>
        </Group>
      </div>
      <Dock />
    </div>
  )
}
