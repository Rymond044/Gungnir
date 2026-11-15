import { useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { post, safeNext } from '../api/client'
import type { Account } from '../api/types'
import { Failure } from '../components/ui'
import ui from '../components/ui.module.css'
import styles from './Auth.module.css'

/** Sign in (ADR 0064). The first password is shown only on the server's machine. */
export default function Login() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [name, setName] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const next = safeNext(params.get('next'))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      const account = await post<Account>('/auth/login', { name, password })
      navigate(account.must_change ? `/password?${new URLSearchParams({ next })}` : next, { replace: true })
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={styles.screen}>
      <form className={styles.form} onSubmit={submit}>
        <span className={styles.brand}>gungnIR</span>
        <label>
          Имя
          <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="username" required />
        </label>
        <label>
          Пароль
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            autoFocus
            required
          />
        </label>
        <button className={`${ui.button} ${ui.primary}`} disabled={busy}>
          Войти
        </button>
        {error !== null && <Failure error={error} />}
      </form>
    </div>
  )
}
