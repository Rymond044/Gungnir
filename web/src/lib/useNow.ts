import { useEffect, useState } from 'react'

/** The current time, ticking every `periodMs`, so live intervals move on. */
export function useNow(periodMs = 10_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), periodMs)
    return () => clearInterval(id)
  }, [periodMs])
  return now
}
