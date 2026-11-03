import { Link, useRouteError } from 'react-router'
import ui from '../components/ui.module.css'

/** A screen that failed to draw: say so and keep the way back, instead of a blank page. */
export default function Crash() {
  const error = useRouteError()
  return (
    <div className={ui.page} style={{ padding: 24 }}>
      <h1>Экран не отрисовался</h1>
      <span className="muted">{error instanceof Error ? error.message : String(error)}</span>
      <Link to="/">На обзор</Link>
    </div>
  )
}
