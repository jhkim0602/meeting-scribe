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

/** 새 댓글을 노트에도 바로 추가할지 */
export const COMMENT_TO_NOTES = 'commentToNotes'
