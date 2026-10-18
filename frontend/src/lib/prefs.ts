// 브라우저에 기억하는 작은 설정값
import { useEffect, useState } from 'react'

const listeners = new Set<() => void>()

export function getPref(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key)
    return v === null ? fallback : v === '1'
  } catch {
    return fallback
  }
}

export function setPref(key: string, value: boolean) {
  try {
    localStorage.setItem(key, value ? '1' : '0')
  } catch {
    /* 무시 */
  }
  listeners.forEach((l) => l())
}

export function usePref(key: string, fallback: boolean): [boolean, (v: boolean) => void] {
  const [v, setV] = useState(() => getPref(key, fallback))
  useEffect(() => {
    const on = () => setV(getPref(key, fallback))
    listeners.add(on)
    return () => {
      listeners.delete(on)
    }
  }, [key, fallback])
  return [v, (nv: boolean) => setPref(key, nv)]
}

/** 참석자 세부 정보(소속·직무·메모) 칸을 펼쳐 둘지 */
export const PEOPLE_DETAILS = 'peopleDetails'

/** 새 댓글을 노트에도 바로 추가할지 */
export const COMMENT_TO_NOTES = 'commentToNotes'

export function getPrefStr<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T | null) ?? fallback
  } catch {
    return fallback
  }
}

export function usePrefStr<T extends string>(key: string, fallback: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => getPrefStr(key, fallback))
  useEffect(() => {
    const on = () => setV(getPrefStr(key, fallback))
    listeners.add(on)
    return () => {
      listeners.delete(on)
    }
  }, [key, fallback])
  return [
    v,
    (nv: T) => {
      try {
        localStorage.setItem(key, nv)
      } catch {
        /* 무시 */
      }
      listeners.forEach((l) => l())
    },
  ]
}

// ⋯ 보기 메뉴
/** 댓글을 한 줄로 접어 보기 */
export const VIEW_COMMENTS_COLLAPSED = 'viewCommentsCollapsed'
/** 녹음 시간 대신 실제 시각 보기 */
export const VIEW_CLOCK = 'viewClock'
/** 기록 글자 크기 */
export const VIEW_FONT = 'viewFont'
export type FontSize = 's' | 'm' | 'l'
/** 녹음 표시줄의 첫 안내를 몇 번 보여 줬는지 */
export const HINT_SEEN = 'hintSeen'

