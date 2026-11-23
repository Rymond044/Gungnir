/** Live processing watermarks tolerate one minute of pipeline delay. */
export function isStale(timestamp: string | null | undefined, now: number): boolean {
  return !timestamp || !Number.isFinite(Date.parse(timestamp)) || now - Date.parse(timestamp) > 60_000
}
