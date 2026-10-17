export function fmtTime(sec: number, forceHours = false): string {
  const s = Math.max(0, Math.floor(sec || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(r).padStart(2, '0')
  return h || forceHours ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

export function fmtDuration(sec: number): string {
  const s = Math.round(sec || 0)
  if (s < 60) return `${s}초`
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h ? `${h}시간 ${m}분` : `${m}분`
}

export function fmtDate(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' }) +
    ' ' + d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })
}

export function fmtRelative(ts: number): string {
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return '방금'
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`
  return new Date(ts * 1000).toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' })
}

export function dateGroup(ts: number): string {
  const d = new Date(ts * 1000)
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000
  if (ts >= start) return '오늘'
  if (ts >= start - 86400) return '어제'
  if (ts >= start - 86400 * 6) return '지난 7일'
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}월`
  return `${d.getFullYear()}년`
}
