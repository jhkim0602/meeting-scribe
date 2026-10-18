import { useEffect, useState } from 'react'
import { MeetingPage } from './components/MeetingPage'
import { RecAlertBar } from './components/RecAlertBar'
import { SettingsDialog } from './components/SettingsDialog'
import { ShortcutsOverlay, useGlobalShortcuts } from './components/Shortcuts'
import { Sidebar } from './components/Sidebar'
import { Toasts } from './components/Toasts'
import { Welcome } from './components/Welcome'
import { useStore } from './lib/store'
import { api } from './lib/api'

function parseHash(): string | null {
  const m = location.hash.match(/^#\/m\/([\w-]+)/)
  return m ? m[1] : null
}

export default function App() {
  const selectedId = useStore((s) => s.selectedId)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const theme = useStore((s) => s.theme)
  const [settingsOpen, setSettingsOpen] = useState(false)
  useGlobalShortcuts()

  useEffect(() => {
    document.documentElement.dataset.theme = theme === 'system' ? '' : theme
  }, [theme])

  useEffect(() => {
    const st = useStore.getState()
    st.loadHealth()
    st.loadSettings()
    st.loadMeetings().then(() => st.select(parseHash()))
    const onHash = () => useStore.getState().select(parseHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  // 앱으로 돌아오면 실시간 모델을 미리 올려 둔다 (오래 쓰지 않아 내려 두었을 때)
  useEffect(() => {
    let last = 0
    const warm = () => {
      if (document.visibilityState !== 'visible' || Date.now() - last < 5 * 60000) return
      last = Date.now()
      api.warmup().then(() => useStore.getState().loadHealth()).catch(() => undefined)
    }
    window.addEventListener('focus', warm)
    document.addEventListener('visibilitychange', warm)
    return () => {
      window.removeEventListener('focus', warm)
      document.removeEventListener('visibilitychange', warm)
    }
  }, [])

  // 작업 진행률/엔진 상태 갱신
  useEffect(() => {
    let lastStatus: string | null = null
    const tick = async () => {
      const st = useStore.getState()
      const { job, meeting, meetings, health } = st
      if (meeting) {
        const running = job && (job.status === 'queued' || job.status === 'running')
        if (running || meeting.status === 'processing') {
          const { job: j } = await api.job(meeting.id).catch(() => ({ job: null }))
          if (j && useStore.getState().selectedId === meeting.id) {
            useStore.setState({ job: j })
            if ((j.status === 'done' || j.status === 'error' || j.status === 'cancelled') && lastStatus !== j.status) {
              await st.refreshMeeting()
              await st.refreshLive()
              await st.loadMeetings()
              const upload = useStore.getState().meeting?.source === 'upload'
              if (j.status === 'done') st.toast(upload ? '받아 적기를 마쳤어요. 글자를 고치고 화자를 고를 수 있어요.' : '원본 스크립트가 준비됐어요. ⋯ 메뉴에서 받을 수 있어요.', 'success')
              if (j.status === 'error') st.toast(`${upload ? '받아 적지' : '원본 스크립트를 만들지'} 못했어요: ${j.error}`, 'error')
            }
            lastStatus = j.status
          }
        }
      }
      if (meetings.some((m) => m.status === 'processing')) st.loadMeetings()
      if (!health || health.live.state === 'loading' || health.live.state === 'idle') st.loadHealth()
    }
    const id = setInterval(tick, 1500)
    return () => clearInterval(id)
  }, [])

  return (
    <div className={`app ${sidebarOpen ? '' : 'side-closed'}`}>
      {sidebarOpen && <Sidebar onSettings={() => setSettingsOpen(true)} />}
      <main className="main">
        {selectedId ? <MeetingPage /> : <Welcome />}
        <RecAlertBar />
      </main>
      {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} />}
      <ShortcutsOverlay />
      <Toasts />
    </div>
  )
}
