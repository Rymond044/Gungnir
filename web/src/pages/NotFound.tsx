import { Link } from 'react-router'
import ui from '../components/ui.module.css'

export default function NotFound() {
  return (
    <div className={ui.page}>
      <h1>Страница не найдена</h1>
      <Link to="/">На обзор</Link>
    </div>
  )
}
