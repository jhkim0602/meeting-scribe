import { ChevronDown, Loader2, Mic, Pause, Play, RotateCcw, RotateCw, Square, StickyNote } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ConfirmStop } from './ConfirmStop'
import { listMics, pauseRecording, resumeRecording, startRecording, stopRecording } from '../hooks/recorder'
import type { Meeting } from '../lib/api'
import { bridges, useStore } from '../lib/store'
import { fmtDuration, fmtTime } from '../lib/time'

function useMicChoice() {
  const [mics, setMics] = useState<MediaDeviceInfo[]>([])
  const [mic, setMic] = useState<string>(() => {
    try {
      return localStorage.getItem('mic') || ''
    } catch {
      return ''
    }
  })
  const refresh = () => listMics().then(setMics)
  useEffect(() => {
    refresh()
    navigator.mediaDevices?.addEventListener?.('devicechange', refresh)
    return () => navigator.mediaDevices?.removeEventListener?.('devicechange', refresh)
  }, [])
  const choose = (id: string) => {
    setMic(id)
    try {
      localStorage.setItem('mic', id)
    } catch {
      /* 무시 */
    }
  }
  return { mics, mic, choose, refresh }
}

function MicSelect({ mics, mic, choose }: { mics: MediaDeviceInfo[]; mic: string; choose: (id: string) => void }) {
  return (
    <label className="mic-select" title="마이크 선택">
      <Mic size={14} />
      <select value={mic} onChange={(e) => choose(e.target.value)}>
        <option value="">기본 마이크</option>
        {mics.filter((m) => m.deviceId && m.deviceId !== 'default').map((m) => (
          <option key={m.deviceId} value={m.deviceId}>{m.label || '마이크'}</option>
        ))}
      </select>
      <ChevronDown size={12} />
    </label>
  )
}

function Level() {
  const level = useStore((s) => s.rec.level)
  const status = useStore((s) => s.rec.status)
  const hist = useRef<number[]>(Array(18).fill(0))
  hist.current = [...hist.current.slice(1), status === 'recording' ? level : 0]
  return (
    <div className="level" aria-hidden>
      {hist.current.map((v, i) => (
        <span key={i} style={{ transform: `scaleY(${Math.max(0.08, Math.min(1, v))})` }} />
      ))}
    </div>
  )
}

function PausedFor({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  return <span className="chip chip-paused">일시정지 · {fmtDuration((now - since) / 1000)} 지남</span>
}

function Recorder() {
  const rec = useStore((s) => s.rec)
  const health = useStore((s) => s.health)
  const engine = health?.live
  const [confirm, setConfirm] = useState(false)
  const stop = async () => {
    setConfirm(false)
    await stopRecording()
  }
  return (
    <div className="dock dock-rec">
      <div className="rec-left">
        {rec.status === 'recording' ? <span className="rec-dot big" /> : <span className="rec-dot big paused" />}
        <span className="rec-time">{fmtTime(rec.elapsed, rec.elapsed >= 3600)}</span>
        <Level />
        {rec.status === 'paused' && rec.pausedAt && <PausedFor since={rec.pausedAt} />}
        {rec.status === 'stopping' && <span className="chip"><Loader2 size={12} className="spin" />마무리 중</span>}
      </div>
      <div className="rec-center">
        {rec.status === 'recording' && (
          <button className="btn btn-round" onClick={() => pauseRecording()} title="일시정지 (쉬는 시간)"><Pause size={18} /></button>
        )}
        {rec.status === 'paused' && (
          <button className="btn btn-resume" onClick={resumeRecording} title="이어서 녹음"><Mic size={16} />이어서 녹음</button>
        )}
        <button className="btn btn-stop" onClick={() => setConfirm(true)} disabled={rec.status === 'stopping' || rec.status === 'connecting'}>
          <Square size={14} fill="currentColor" />
          녹음 종료
        </button>
      </div>
      <div className="rec-right">
        {engine?.state === 'loading' && <span className="engine-note"><Loader2 size={12} className="spin" />모델 준비 중 · 녹음은 계속 저장돼요</span>}
        {engine?.state === 'error' && <span className="engine-note err" title={engine.error || ''}>실시간 받아쓰기 오류 · 녹음은 저장 중</span>}
        {engine?.state === 'ready' && rec.status === 'recording' && <span className="engine-note ok"><StickyNote size={12} />글자를 드래그하고 C를 누르면 댓글</span>}
        {rec.status === 'paused' && <span className="engine-note ok">일시정지 중에는 기록되지 않아요</span>}
      </div>
      {confirm && (
        <ConfirmStop
          onClose={() => setConfirm(false)}
          onStop={stop}
          onPause={() => {
            setConfirm(false)
            pauseRecording()
          }}
        />
      )}
    </div>
  )
}

const SPEEDS = [1, 1.25, 1.5, 2]

function Player({ meeting, onResume }: { meeting: Meeting; onResume?: () => void }) {
  const audio = useRef<HTMLAudioElement>(null)
  const set = useStore((s) => s.set)
  const playing = useStore((s) => s.playing)
  const time = useStore((s) => s.playerTime)
  const [duration, setDuration] = useState(meeting.duration || 0)
  const [speed, setSpeed] = useState(1)

  useEffect(() => {
    bridges.player = {
      seek: (t, play = true) => {
        const el = audio.current
        if (!el) return
        el.currentTime = Math.max(0, t)
        set({ playerTime: el.currentTime })
        if (play) el.play().catch(() => undefined)
      },
      time: () => audio.current?.currentTime ?? 0,
    }
    return () => {
      bridges.player = null
    }
  }, [set])

  const toggle = () => {
    const el = audio.current
    if (!el) return
    if (el.paused) el.play().catch(() => undefined)
    else el.pause()
  }
  const skip = (d: number) => {
    const el = audio.current
    if (el) el.currentTime = Math.max(0, Math.min(el.duration || 0, el.currentTime + d))
  }

  return (
    <div className="dock dock-player">
      <audio
        ref={audio}
        src={`/api/meetings/${meeting.id}/audio?d=${Math.round(meeting.duration)}`}
        preload="metadata"
        onPlay={() => set({ playing: true })}
        onPause={() => set({ playing: false })}
        onTimeUpdate={(e) => set({ playerTime: e.currentTarget.currentTime })}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || meeting.duration)}
      />
      <div className="player-controls">
        <button className="icon-btn lg" onClick={() => skip(-10)} title="10초 뒤로"><RotateCcw size={17} /></button>
        <button className="btn btn-round play" onClick={toggle} title={playing ? '일시정지' : '재생'}>
          {playing ? <Pause size={18} /> : <Play size={18} />}
        </button>
        <button className="icon-btn lg" onClick={() => skip(10)} title="10초 앞으로"><RotateCw size={17} /></button>
      </div>
      <span className="player-time">{fmtTime(time, duration >= 3600)}</span>
      <input
        className="scrubber"
        type="range"
        min={0}
        max={duration || 1}
        step={0.1}
        value={Math.min(time, duration || 0)}
        style={{ ['--p' as string]: `${duration ? (time / duration) * 100 : 0}%` }}
        onChange={(e) => bridges.player?.seek(Number(e.target.value), false)}
      />
      <span className="player-time muted">{fmtTime(duration, duration >= 3600)}</span>
      <button
        className="speed"
        onClick={() => {
          const next = SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length]
          setSpeed(next)
          if (audio.current) audio.current.playbackRate = next
        }}
        title="재생 속도"
      >
        {speed}x
      </button>
      {onResume && (
        <button className="btn btn-sm btn-ghost resume" onClick={onResume} title="같은 회의에 이어서 녹음">
          <span className="rec-dot" />이어서 녹음
        </button>
      )}
    </div>
  )
}

export function Dock() {
  const meeting = useStore((s) => s.meeting)!
  const rec = useStore((s) => s.rec)
  const toast = useStore((s) => s.toast)
  const { mics, mic, choose, refresh } = useMicChoice()
  const recordingHere = rec.status !== 'idle' && rec.meetingId === meeting.id
  const busyElsewhere = rec.status !== 'idle' && rec.meetingId !== meeting.id

  const start = async () => {
    if (busyElsewhere) {
      toast('다른 회의를 녹음 중이에요. 먼저 종료해 주세요.', 'error')
      return
    }
    try {
      await startRecording(meeting.id, mic || undefined)
      refresh()
    } catch {
      /* 안내는 recorder에서 */
    }
  }

  if (recordingHere) return <Recorder />
  if (meeting.has_audio) {
    // 원본을 만드는 중이어도 이어서 녹음할 수 있다 (서버가 그 작업을 취소한다)
    const canResume = meeting.source === 'live'
    return <Player key={meeting.id} meeting={meeting} onResume={canResume ? start : undefined} />
  }
  return (
    <div className="dock dock-idle">
      <button className="btn btn-record" onClick={start} disabled={rec.status === 'connecting'}>
        {rec.status === 'connecting' ? <Loader2 size={16} className="spin" /> : <span className="rec-dot white" />}
        녹음 시작
      </button>
      <MicSelect mics={mics} mic={mic} choose={choose} />
      <span className="muted dock-hint">말하는 동안 실시간으로 받아 적고, 끝나면 화자를 나눈 원본을 만들어요.</span>
    </div>
  )
}
