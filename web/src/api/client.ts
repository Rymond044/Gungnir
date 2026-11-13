// The server's HTTP API, same origin: nginx (or the Vite dev server)
// proxies /api to the server.

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Param = string | number | boolean | null | undefined | string[]

export function query(params: Record<string, Param>): string {
  const q = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) continue
    if (Array.isArray(value)) value.forEach((v) => q.append(key, v))
    else q.append(key, String(value))
  }
  const text = q.toString()
  return text ? `?${text}` : ''
}

export async function get<T>(path: string, params: Record<string, Param> = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}${query(params)}`, { headers: { Accept: 'application/json' } })
  return read<T>(response)
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return read<T>(response)
}

async function read<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Not JSON: keep the status text.
    }
    throw new ApiError(response.status, detail)
  }
  return response.json() as Promise<T>
}
