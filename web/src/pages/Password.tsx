import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { post, safeNext } from '../api/client'
import { useMe } from '../api/hooks'
import { Failure } from '../components/ui'
import ui from '../components/ui.module.css'
import styles from './Auth.module.css'

const MIN_LENGTH = 10

/** A new password; the initial one must be changed before anything else (ADR 0064). */
export default function Password() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const [params] = useSearchParams()
  const me = useMe()
  const [old, setOld] = useState('')
  const [fresh, setFresh] = useState('')
  const [again, setAgain] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const mismatch = again !== '' && again !== fresh

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (mismatch) return
    setBusy(true)
    try {
      await post('/auth/password', { old, new: fresh })
      await client.invalidateQueries()
      navigate(safeNext(params.get('next')), { replace: true })
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={styles.screen}>
      <form className={styles.form} onSubmit={submit}>
        <span className={styles.brand}>{me.data?.must_change ? 'Задайте свой пароль' : 'Смена пароля'}</span>
        <label>
          Текущий пароль
          <input
            type="password"
            value={old}
            onChange={(e) => setOld(e.target.value)}
            autoComplete="current-password"
            autoFocus
            required
          />
        </label>
        <label>
          Новый пароль
          <input
            type="password"
            value={fresh}
            onChange={(e) => setFresh(e.target.value)}
            autoComplete="new-password"
            minLength={MIN_LENGTH}
            required
          />
        </label>
        <label>
          Повтор
          <input
            type="password"
            value={again}
            onChange={(e) => setAgain(e.target.value)}
            autoComplete="new-password"
            required
          />
        </label>
        {mismatch && <span className={styles.hint}>Пароли не совпадают.</span>}
        <button className={`${ui.button} ${ui.primary}`} disabled={busy || mismatch}>
          Сменить
        </button>
        {error !== null && <Failure error={error} />}
      </form>
    </div>
  )
}
