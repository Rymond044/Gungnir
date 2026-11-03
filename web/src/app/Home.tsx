import { Navigate } from 'react-router'
import { useHosts } from '../api/hooks'
import { Empty, Failure, Loading } from '../components/ui'

/** Opens the host whose agent was seen last. */
export default function Home() {
  const hosts = useHosts()
  if (hosts.isPending) return <Loading />
  if (hosts.isError) return <Failure error={hosts.error} />
  const last = [...hosts.data].sort((a, b) => (b.last_seen_at ?? '').localeCompare(a.last_seen_at ?? ''))[0]
  if (!last) return <Empty>Хостов пока нет. Добавьте хост: gungnir-server host add &lt;имя&gt;.</Empty>
  return <Navigate to={`/hosts/${last.name}`} replace />
}
