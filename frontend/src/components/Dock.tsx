import { Check, ChevronDown, Coffee, HardDrive, Loader2, Mic, MicOff, Pause, Play, RotateCcw, RotateCw, Square, WifiOff } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ConfirmStop } from './ConfirmStop'
import { Floating } from './Floating'
import { listMics, pauseRecording, resumeRecording, startRecording, stopRecording, switchMic } from '../hooks/recorder'
import type { Meeting } from '../lib/api'
import { getPrefStr, HINT_SEEN } from '../lib/prefs'
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

type MicChoice = ReturnType<typeof useMicChoice>

// 블루투스 마이크는 통화 모드(낮은 음질)로 바뀌어 받아쓰기가 부정확해진다
const BLUETOOTH = /airpods|bluetooth|buds|beats|hands-?free|headset|블루투스/i

function micLabel(mics: MediaDeviceInfo[], mic: string) {
  const d = mics.find((m) => m.deviceId === (mic || 'default'))
  return d?.label || ''
}

function isBluetooth(mics: MediaDeviceInfo[], mic: string) {
  return BLUETOOTH.test(micLabel(mics, mic))
}

/** 녹음 전 마이크 시험: 잠깐 마이크를 열어 소리 크기만 잰다 */
function useMicTest(deviceId: string, active: boolean, onOpen: () => void) {
  const [level, setLevel] = useState(0)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!active) return
    let stopped = false
    let stream: MediaStream | null = null
    let ctx: AudioContext | null = null
    let raf = 0
    let frame = 0
    navigator.mediaDevices
      .getUserMedia({ audio: { deviceId: deviceId ? { exact: deviceId } : undefined } })
      .then((st) => {
        if (stopped) {
          st.getTracks().forEach((t) => t.stop())
          return
        }
        stream = st
        onOpen() // 권한을 받은 뒤라 마이크 이름을 읽을 수 있다
        ctx = new AudioContext()
        const an = ctx.createAnalyser()
        an.fftSize = 1024
        ctx.createMediaStreamSource(st).connect(an)
        const buf = new Float32Array(an.fftSize)
        const tick = () => {
          if (frame++ % 4 === 0) {
            an.getFloatTimeDomainData(buf)
            let sq = 0
            for (const x of buf) sq += x * x
            setLevel(Math.min(1, Math.sqrt(sq / buf.length) * 6))
          }
          raf = requestAnimationFrame(tick)
        }
        tick()
      })
      .catch(() => setFailed(true))
    return () => {
      stopped = true
      cancelAnimationFrame(raf)
      stream?.getTracks().forEach((t) => t.stop())
      ctx?.close().catch(() => undefined)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId, active])
  return { level, failed }
}

function Meter({ level }: { level: number }) {
  return (
    <div className="meter" aria-hidden>
      <span style={{ width: `${Math.max(2, Math.min(100, level * 100))}%` }} />
    </div>
  )
}

/** 마이크 고르기 + 입력 확인. 녹음 중에는 연결 상태도 함께 보여 준다. */
function MicPanel({ rect, onClose, choice, recording }: { rect: DOMRect; onClose: () => void; choice: MicChoice; recording: boolean }) {
  const { mics, mic, choose, refresh } = choice
  const recLevel = useStore((s) => (s.rec.status === 'recording' ? s.rec.level : 0))
  const net = useStore((s) => s.rec.net)
  const health = useStore((s) => s.health)
  const test = useMicTest(mic, !recording, refresh)
  const level = recording ? recLevel : test.level
  const devices = mics.filter((m) => m.deviceId && m.deviceId !== 'default')
  const pick = (id: string) => {
    if (id === mic) return
    choose(id)
    if (recording) switchMic(id || undefined)
  }
  useEffect(() => {
    useStore.getState().loadHealth()
  }, [])
  const hours = health ? health.disk_free_s / 3600 : null
  return (
    <Floating rect={rect} placement="above" onClose={onClose} className="mic-panel">
      <p className="mic-panel-label">마이크</p>
      <button className="menu-item check-item" onClick={() => pick('')} aria-checked={!mic} role="menuitemradio">
        <span className="check-box">{!mic && <Check size={13} />}</span>
        기본 마이크{micLabel(mics, '') && <em>{micLabel(mics, '').replace(/^(Default|기본값) - /, '')}</em>}
      </button>
      {devices.map((d) => (
        <button key={d.deviceId} className="menu-item check-item" onClick={() => pick(d.deviceId)} aria-checked={mic === d.deviceId} role="menuitemradio">
          <span className="check-box">{mic === d.deviceId && <Check size={13} />}</span>
          {d.label || '마이크'}
        </button>
      ))}
      <div className="mic-panel-meter">
        <Meter level={level} />
        <span className="muted">{test.failed && !recording ? '마이크를 열 수 없어요. 권한을 확인해 주세요.' : level > 0.04 ? '잘 들려요' : '말해 보세요'}</span>
      </div>
      {isBluetooth(mics, mic) && <p className="mic-panel-note warn-hint">블루투스 마이크는 통화 음질로 바뀌어 받아쓰기가 부정확해질 수 있어요. 맥북 내장 마이크를 권해요.</p>}
      {recording && health && (
        <dl className="mic-panel-status">
          <dt>실시간 받아쓰기</dt>
          <dd>{health.live.state === 'ready' ? '준비됨' : health.live.state === 'error' ? '오류 · 녹음은 저장 중' : '준비 중 · 녹음은 저장 중'}</dd>
          <dt>서버 연결</dt>
          <dd>{net === 'ok' ? '연결됨' : '다시 연결 중 · 소리는 모아 두는 중'}</dd>
          <dt>저장 공간</dt>
          <dd>{hours === null ? '-' : hours >= 1 ? `약 ${Math.floor(hours)}시간 더 녹음할 수 있어요` : `약 ${Math.max(1, Math.round(hours * 60))}분 남았어요`}</dd>
          <dt>잠자기</dt>
          <dd>{health.keep_awake ? '녹음하는 동안 맥이 잠들지 않아요' : '화면이 꺼지지 않게 해 두었어요'}</dd>
        </dl>
      )}
    </Floating>
  )
}

function PausedFor({ since, heard }: { since: number; heard: boolean }) {
  const [now, setNow] = useState(Date.now())
  const setRec = useStore((s) => s.setRec)
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <span className="chip chip-paused">
      일시정지 · {fmtDuration((now - since) / 1000)} 지남
      {heard && (
        <button
          className="heard-dot"
          title="일시정지 중에 말소리가 들렸어요"
          aria-label="일시정지 중에 말소리가 들렸어요"
          onClick={() => setRec({ alert: { kind: 'paused-speech' }, alertCollapsed: false })}
        />
      )}
    </span>
  )
}

/** 마이크 아이콘: 소리가 들어오면 은은하게 빛나고, 문제가 있으면 주황/빨강 */
function MicRing() {
  const level = useStore((s) => (s.rec.status === 'recording' ? s.rec.level : 0))
  const mic = useStore((s) => s.rec.mic)
  return (
    <span
      className={`mic-ring ${mic !== 'ok' ? `sig ${mic}` : ''}`}
      style={{ ['--lv' as string]: Math.min(1, level * 1.4).toFixed(2) }}
      title={mic === 'bad' ? '마이크를 찾을 수 없어요' : mic === 'warn' ? '마이크 소리가 들어오지 않거나 다시 연결하는 중이에요' : undefined}
    >
      {mic === 'ok' ? <Mic size={14} /> : <MicOff size={14} />}
    </span>
  )
}

/** 표시줄 옆 상태: 연결 끊김 아이콘, 접어 둔 알림. 문제가 있을 때만 보인다. */
function Signals() {
  const net = useStore((s) => s.rec.net)
  const alert = useStore((s) => s.rec.alert)
  const collapsed = useStore((s) => s.rec.alertCollapsed)
  const setRec = useStore((s) => s.setRec)
  const expand = () => setRec({ alertCollapsed: false })
  return (
    <>
      {net !== 'ok' && (
        <span className="sig warn" title="서버에 다시 연결하는 중이에요. 소리는 브라우저에 모아 두고 있어요.">
          <WifiOff size={15} />
        </span>
      )}
      {alert && collapsed && (
        <button className={`sig sig-btn ${alert.kind === 'silence' ? 'info' : 'warn'}`} onClick={expand} title="알림 다시 보기">
          {alert.kind === 'silence' ? <Coffee size={15} /> : alert.kind === 'disk-low' ? <HardDrive size={15} /> : <MicOff size={15} />}
        </button>
      )}
    </>
  )
}

function Recorder({ choice }: { choice: MicChoice }) {
  const rec = useStore((s) => s.rec)
  const engine = useStore((s) => s.health?.live)
  const [confirm, setConfirm] = useState(false)
  const [panel, setPanel] = useState<DOMRect | null>(null)
  // 처음 몇 번만 사용법을 한 줄 보여 준다
  const [showHint] = useState(() => Number(getPrefStr(HINT_SEEN, '0')) <= 3)
  const stop = async () => {
    setConfirm(false)
    await stopRecording()
  }
  return (
    <div className="dock dock-rec">
      <div className="rec-left">
        <button className={`rec-pill ${rec.status}`} onClick={(e) => setPanel(e.currentTarget.getBoundingClientRect())} title="마이크·연결 상태">
          <span className={`rec-dot big ${rec.status === 'recording' ? '' : 'paused'}`} />
          <span className="rec-time">{fmtTime(rec.elapsed, rec.elapsed >= 3600)}</span>
          <MicRing />
          <ChevronDown size={12} className="rec-pill-caret" />
        </button>
        <Signals />
        {rec.status === 'paused' && rec.pausedAt && <PausedFor since={rec.pausedAt} heard={rec.heard} />}
        {rec.status === 'stopping' && <span className="chip"><Loader2 size={12} className="spin" />마무리 중</span>}
      </div>
      <div className="rec-center">
        {rec.status === 'recording' && (
          <button className="btn btn-round" onClick={() => pauseRecording()} title="일시정지 (쉬는 시간) · ⌘⇧P"><Pause size={18} /></button>
        )}
        {rec.status === 'paused' && (
          <button className="btn btn-resume" onClick={resumeRecording} title="이어서 녹음 · ⌘⇧P"><Mic size={16} />이어서 녹음</button>
        )}
        <button className="btn btn-stop" onClick={() => setConfirm(true)} disabled={rec.status === 'stopping' || rec.status === 'connecting'}>
          <Square size={14} fill="currentColor" />
          녹음 종료
        </button>
      </div>
      <div className="rec-right">
        {engine?.state === 'loading' && <span className="engine-note"><Loader2 size={12} className="spin" />모델 준비 중 · 녹음은 계속 저장돼요</span>}
        {engine?.state === 'error' && <span className="engine-note err" title={engine.error || ''}>실시간 받아쓰기 오류 · 녹음은 저장 중</span>}
        {engine?.state === 'ready' && rec.status === 'recording' && showHint && <span className="engine-note ok">글자를 드래그하고 C를 누르면 댓글 · ? 단축키</span>}
        {rec.status === 'paused' && <span className="engine-note ok">일시정지 중에는 기록되지 않아요</span>}
      </div>
      {panel && <MicPanel rect={panel} onClose={() => setPanel(null)} choice={choice} recording />}
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

/** 재생 막대 위의 옅은 눈금: 댓글(노랑), 구분선(회색). 지금 보고 있는 화면 기준. */
function ScrubberMarks({ duration }: { duration: number }) {
  const anns = useStore((s) => (s.tab === 'final' ? s.final?.annotations : s.live?.annotations))
  const marks = useMemo(() => {
    if (!anns || !duration) return []
    return anns
      .filter((a) => (a.kind === 'comment' || a.kind === 'divider') && a.pos)
      .map((a) => ({ id: a.id, kind: a.kind, p: Math.min(100, Math.max(0, ((a.anchor.t0 ?? a.anchor.t ?? 0) / duration) * 100)) }))
  }, [anns, duration])
  if (!marks.length) return null
  return (
    <div className="scrub-marks" aria-hidden>
      {marks.map((m) => <span key={m.id} className={`scrub-mark mk-${m.kind}`} style={{ left: `${m.p}%` }} />)}
    </div>
  )
}

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
      <div className="scrub-wrap">
        <ScrubberMarks duration={duration} />
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
      </div>
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
  const choice = useMicChoice()
  const { mics, mic, refresh } = choice
  const [panel, setPanel] = useState<DOMRect | null>(null)
  const recordingHere = rec.status !== 'idle' && rec.meetingId === meeting.id
  const busyElsewhere = rec.status !== 'idle' && rec.meetingId !== meeting.id

  const start = async () => {
    if (busyElsewhere) {
      toast('다른 회의를 녹음 중이에요. 먼저 종료해 주세요.', 'error')
      return
    }
    setPanel(null)
    try {
      await startRecording(meeting.id, mic || undefined)
      try {
        localStorage.setItem(HINT_SEEN, String(Number(getPrefStr(HINT_SEEN, '0')) + 1))
      } catch {
        /* 무시 */
      }
      refresh()
    } catch {
      /* 안내는 recorder에서 */
    }
  }

  if (recordingHere) return <Recorder choice={choice} />
  if (meeting.has_audio) {
    // 원본을 만드는 중이어도 이어서 녹음할 수 있다 (서버가 그 작업을 취소한다)
    const canResume = meeting.source === 'live'
    return <Player key={meeting.id} meeting={meeting} onResume={canResume ? start : undefined} />
  }
  const label = mic ? micLabel(mics, mic) || '마이크' : '기본 마이크'
  return (
    <div className="dock dock-idle">
      <button className="btn btn-record" onClick={start} disabled={rec.status === 'connecting'}>
        {rec.status === 'connecting' ? <Loader2 size={16} className="spin" /> : <span className="rec-dot white" />}
        녹음 시작
      </button>
      <button className="mic-select" onClick={(e) => setPanel(e.currentTarget.getBoundingClientRect())} title="마이크 고르기·소리 확인">
        <Mic size={14} />
        <span className="mic-name">{label}</span>
        <ChevronDown size={12} />
      </button>
      {isBluetooth(mics, mic) ? (
        <span className="dock-hint warn-hint">블루투스 마이크는 통화 음질로 바뀌어 받아쓰기가 부정확해질 수 있어요. 맥북 내장 마이크를 권해요.</span>
      ) : (
        <span className="muted dock-hint">말하는 동안 실시간으로 받아 적고, 끝나면 화자를 나눈 원본을 만들어요.</span>
      )}
      {panel && <MicPanel rect={panel} onClose={() => setPanel(null)} choice={choice} recording={false} />}
    </div>
  )
}
