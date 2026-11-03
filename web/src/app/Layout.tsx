import { NavLink, Outlet, useNavigate, useParams } from 'react-router'
import { useHosts, useOverview } from '../api/hooks'
import { age } from '../lib/format'
import { classNames, named } from '../lib/names'
import { useNow } from '../lib/useNow'
import { AnalysisIcon, AnomalyIcon, KnowledgeIcon, OverviewIcon } from './icons'
import styles from './Layout.module.css'

// The agent sends every 10 s; a minute of silence means it is not reaching
// the server.
const STALE_S = 60

export default function Layout() {
  const { host = '' } = useParams()
  const navigate = useNavigate()
  const hosts = useHosts()
  const overview = useOverview(host)
  const now = useNow(5000)
  const o = overview.data

  const link = ({ isActive }: { isActive: boolean }) => `${styles.link} ${isActive ? styles.active : ''}`
  const seen = o?.host.last_seen_at ? (now - Date.parse(o.host.last_seen_at)) / 1000 : null
  const stale = seen === null || seen > STALE_S
  const working = o?.collectors.filter((c) => c.state !== 'disabled').length ?? 0
  const open = (o?.open.anomalies ?? 0) + (o?.open.impact ?? 0)

  return (
    <div className={styles.shell}>
      <aside className={styles.side}>
        <NavLink to={`/hosts/${host}`} className={styles.brand}>
          gungnIR
        </NavLink>
        <nav className={styles.nav} aria-label="Разделы">
          <NavLink to={`/hosts/${host}`} end className={link}>
            <OverviewIcon />
            Обзор
          </NavLink>
          <NavLink to={`/hosts/${host}/anomalies`} className={link}>
            <AnomalyIcon />
            Аномалии
            {open > 0 && (
              <span
                className={`${styles.badge} ${o?.open.impact ? '' : styles.badgeQuiet}`}
                title="открытые аномалии и записи влияния"
              >
                {open}
              </span>
            )}
          </NavLink>
          <NavLink to={`/hosts/${host}/analysis`} className={link}>
            <AnalysisIcon />
            Анализ
          </NavLink>
          <NavLink to={`/hosts/${host}/knowledge`} className={link}>
            <KnowledgeIcon />
            База знаний
          </NavLink>
        </nav>
        <div className={styles.foot}>
          {hosts.data && hosts.data.length > 1 ? (
            <select
              className={styles.select}
              aria-label="Хост"
              value={host}
              onChange={(e) => navigate(`/hosts/${e.target.value}`)}
            >
              {hosts.data.map((h) => (
                <option key={h.name} value={h.name}>
                  {h.name}
                </option>
              ))}
            </select>
          ) : (
            <span className={styles.hostName}>{host}</span>
          )}
          <span className={styles.contact}>
            <span className={`${styles.led} ${stale ? styles.ledStale : ''}`} />
            {seen === null ? 'агент ещё не выходил на связь' : `агент на связи ${age(seen)}`}
          </span>
          {o && <span>нагрузка: {named(classNames, o.workload_class ?? 'unknown')}</span>}
          {o && (
            <NavLink to={`/hosts/${host}/collectors`}>
              коллекторы: {working} из {o.collectors.length}
            </NavLink>
          )}
        </div>
      </aside>
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  )
}
