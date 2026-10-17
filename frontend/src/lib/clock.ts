// 녹음 시간(초) → 실제 시각. 녹음을 시작하거나 재개할 때마다 서버가 기준점을 남긴다.

export type Clock = [number, number][]

/** 녹음 시간 t에 해당하는 실제 시각(epoch 초). 기준점이 없으면(업로드 파일) null. */
export function wallAt(clock: Clock | undefined, t: number): number | null {
  if (!clock || clock.length === 0) return null
  let base = clock[0]
  for (const p of clock) {
    if (p[0] <= t + 1e-3) base = p
    else break
  }
  return base[1] + (t - base[0])
}

/** 오후 2:03 (seconds면 오후 2:03:15) */
export function fmtClock(wall: number, seconds = false): string {
  return new Date(wall * 1000).toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit', second: seconds ? '2-digit' : undefined })
}
