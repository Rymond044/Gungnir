// Russian names the interface shows, in the words Linux operators use
// (ADR 0053). The key is always shown next to a name in monospace, so the
// link to the code and the knowledge base stays visible. A key without a
// name here falls back to the knowledge base's English label, then to the
// key itself.

export const signalNames: Record<string, string> = {
  'agent.anon_bytes': 'Память агента',
  'agent.cpu_cores': 'CPU агента',
  'agent.rss_bytes': 'Память агента (RSS)',
  'cgroup.cpu_cores': 'CPU группы',
  'cgroup.cpu_pressure_some_ratio': 'Давление CPU в группе (some)',
  'cgroup.io_pressure_full_ratio': 'Давление I/O в группе (full)',
  'cgroup.io_pressure_some_ratio': 'Давление I/O в группе (some)',
  'cgroup.mem_bytes': 'Память группы',
  'cgroup.mem_high_events': 'Срабатывания memory.high',
  'cgroup.mem_max_events': 'Срабатывания memory.max',
  'cgroup.mem_pressure_full_ratio': 'Давление памяти в группе (full)',
  'cgroup.mem_pressure_some_ratio': 'Давление памяти в группе (some)',
  'cgroup.oom_kills': 'OOM-kill в группе',
  'cgroup.read_bytes_per_s': 'Чтение группы',
  'cgroup.read_ops_per_s': 'IOPS чтения группы',
  'cgroup.throttled_ratio': 'Троттлинг группы по cpu.max',
  'cgroup.write_bytes_per_s': 'Запись группы',
  'cgroup.write_ops_per_s': 'IOPS записи группы',
  'cpu.blocked_tasks': 'Задачи в состоянии D',
  'cpu.context_switches_per_s': 'Переключения контекста',
  'cpu.forks_per_s': 'Создание процессов (fork)',
  'cpu.idle_ratio': 'Простой CPU (idle)',
  'cpu.iowait_ratio': 'Ожидание I/O (iowait)',
  'cpu.irq_ratio': 'Прерывания (irq, softirq)',
  'cpu.nice_ratio': 'CPU с низким приоритетом (nice)',
  'cpu.pressure_some_ratio': 'Давление CPU (PSI some)',
  'cpu.runnable_tasks': 'Очередь на CPU',
  'cpu.steal_ratio': 'Время, отнятое гипервизором (steal)',
  'cpu.system_ratio': 'CPU ядра (system)',
  'cpu.temperature_c': 'Температура CPU',
  'cpu.thermal_throttles': 'События теплового троттлинга',
  'cpu.user_ratio': 'CPU пользователя (user)',
  'disk.in_flight': 'Запросы в обработке',
  'disk.queue_depth': 'Глубина очереди',
  'disk.read_await_ms': 'Задержка чтения (await)',
  'disk.read_bytes_per_s': 'Чтение с диска',
  'disk.read_ops_per_s': 'IOPS чтения',
  'disk.read_request_bytes': 'Средний запрос чтения',
  'disk.util_ratio': 'Загрузка диска (util)',
  'disk.write_await_ms': 'Задержка записи (await)',
  'disk.write_bytes_per_s': 'Запись на диск',
  'disk.write_ops_per_s': 'IOPS записи',
  'disk.write_request_bytes': 'Средний запрос записи',
  'fs.available_bytes': 'Свободно на ФС',
  'fs.inodes_used_ratio': 'Занято inode',
  'fs.used_ratio': 'Заполнение ФС',
  'io.pressure_full_ratio': 'Давление I/O (PSI full)',
  'io.pressure_some_ratio': 'Давление I/O (PSI some)',
  'mem.anon_bytes': 'Анонимная память',
  'mem.available_bytes': 'Доступная память',
  'mem.cached_bytes': 'Страничный кэш',
  'mem.direct_reclaims_per_s': 'Прямое освобождение памяти (direct reclaim)',
  'mem.dirty_background_bytes': 'Порог фонового сброса',
  'mem.dirty_bytes': 'Грязные страницы',
  'mem.free_bytes': 'Свободная память',
  'mem.major_faults_per_s': 'Страничные промахи с чтением с диска (major faults)',
  'mem.oom_kills': 'OOM-kill',
  'mem.pressure_full_ratio': 'Давление памяти (PSI full)',
  'mem.pressure_some_ratio': 'Давление памяти (PSI some)',
  'mem.shmem_bytes': 'Shmem и tmpfs',
  'mem.slab_unreclaimable_bytes': 'Неосвобождаемый slab',
  'mem.used_ratio': 'Занятая память',
  'mem.writeback_bytes': 'Страницы под записью (writeback)',
  'power.battery_ratio': 'Заряд батареи',
  'power.on_ac': 'Питание от сети',
  'proc.count': 'Процессы',
  'proc.threads': 'Потоки',
  'swap.in_pages_per_s': 'Подкачка из swap (swap-in)',
  'swap.out_pages_per_s': 'Вытеснение в swap (swap-out)',
  'swap.used_bytes': 'Занято в swap',
  'swap.used_ratio': 'Заполнение swap',
}

export const causeNames: Record<string, string> = {
  bulk_reader: 'Массовое чтение',
  bulk_sequential_writer: 'Массовая последовательная запись',
  cpu_hog: 'Захват CPU',
  cpu_quota_throttling: 'Троттлинг по квоте CPU',
  cpu_steal: 'CPU забирает гипервизор (steal)',
  device_errors: 'Ошибки устройства',
  filesystem_filled: 'Процесс заполняет ФС',
  filesystem_full: 'ФС заполнена',
  filesystem_maintenance: 'Обслуживание ФС',
  group_memory_limit: 'Группа упёрлась в memory.max',
  group_memory_throttled: 'Троттлинг группы по memory.high',
  interrupt_storm: 'Шторм прерываний',
  kernel_memory_growth: 'Рост памяти ядра',
  memory_exhaustion: 'Нехватка памяти, OOM',
  memory_hog: 'Захват памяти',
  memory_leak: 'Утечка памяти',
  page_cache_thrashing: 'Трешинг страничного кэша',
  process_churn: 'Частый запуск процессов',
  small_random_io: 'Мелкий случайный I/O',
  swap_io: 'Активный свопинг',
  sync_write_storm: 'Шторм синхронной записи',
  thermal_throttling: 'Тепловой троттлинг',
  tmpfs_growth: 'Рост tmpfs',
  writeback_storm: 'Шторм сброса грязных страниц',
}

export const eventNames: Record<string, string> = {
  block_io_error: 'Ошибка блочного I/O',
  btrfs_device_errors: 'Ошибки устройства Btrfs',
  hung_task: 'Зависшая задача (hung task)',
  memcg_oom_kill: 'OOM kill в группе',
  nvme_timeout: 'Таймаут команды NVMe',
  oom_kill: 'OOM kill ядра',
  unit_failed: 'Юнит упал',
  unit_oomd_kill: 'Юнит завершён systemd-oomd',
  unit_out_of_memory: 'Юнит завершён за нехватку памяти',
}

export const processNames: Record<string, string> = {
  btrfs_cleaner: 'Очистка Btrfs',
  btrfs_transaction: 'Коммит транзакции Btrfs',
  jbd2: 'Журнал ext4 (jbd2)',
  ksoftirqd: 'Потоки softirq',
  kswapd: 'kswapd освобождает память',
  waiting_on_io: 'Процесс ждёт I/O (D)',
  writeback_flusher: 'Поток сброса работает',
}

export const actionNames: Record<string, string> = {
  cpu_max: 'Ограничить CPU',
  cpu_weight: 'Снизить вес CPU',
  io_max_rbps: 'Ограничить скорость чтения',
  io_max_riops: 'Ограничить IOPS чтения',
  io_max_wbps: 'Ограничить скорость записи',
  io_max_wiops: 'Ограничить IOPS записи',
  io_weight: 'Снизить вес I/O',
  ionice: 'Снизить приоритет I/O',
  memory_high: 'Ограничить память (memory.high)',
  renice: 'Снизить приоритет планирования',
}

export const protectedNames: Record<string, string> = {
  actuator: 'Исполнитель gungnIR',
  agent: 'Агент gungnIR',
  dbus: 'Шина D-Bus',
  init: 'systemd (PID 1)',
  journald: 'Журнал systemd',
  logind: 'logind',
  user_manager: 'Пользовательский systemd',
}

export const factNames: Record<string, string> = {
  'cgroup.io_accounting': 'Учёт I/O по группам',
  'disk.iocost': 'Контроллер iocost',
  'disk.rotational': 'Вращающийся диск',
  'disk.scheduler': 'Планировщик I/O',
  'power.profile': 'Профиль питания',
  'swap.enabled': 'Swap включён',
  'workload.class': 'Тип нагрузки',
}

export const resourceNames: Record<string, string> = {
  cpu: 'CPU',
  disk: 'Диск',
  filesystem: 'Файловая система',
  memory: 'Память',
  pids: 'Таблица процессов',
  power: 'Питание',
  swap: 'Swap',
}

export const entityTypeNames: Record<string, string> = {
  block_device: 'блочное устройство',
  control_group: 'группа (cgroup)',
  host: 'хост',
  mount_point: 'точка монтирования',
  process: 'процесс',
}

export const unitNames: Record<string, string> = {
  '1': 'доля',
  '1/s': 'в секунду',
  bytes: 'байты',
  'bytes/s': 'байт/с',
  cores: 'ядра',
  degC: '°C',
  events: 'события',
  kills: 'завершения',
  ms: 'мс',
  'pages/s': 'страниц/с',
  processes: 'процессы',
  requests: 'запросы',
  tasks: 'задачи',
  threads: 'потоки',
}

const profileNames: Record<string, string> = {
  eco: 'экономичный',
  balanced: 'сбалансированный',
  performance: 'производительный',
}

/** A power profile as the agent reports it, `profile/source`, in words. */
export function powerName(power: string | null | undefined): string {
  if (!power) return '—'
  const [profile, source] = power.split('/')
  const from = source === 'battery' ? 'от батареи' : source === 'ac' ? 'от сети' : source
  return [profileNames[profile] ?? profile, from].filter(Boolean).join(', ')
}

export const domainNames: Record<string, string> = {
  disk: 'Диск и ФС',
  memory: 'Память',
  cpu: 'CPU',
  cross: 'Перекрёстные эффекты',
}

export const roleNames: Record<string, string> = {
  culprit: 'виновник',
  device: 'устройство',
  filesystem: 'файловая система',
  host: 'хост',
  others: 'другие программы',
}

export const classNames: Record<string, string> = {
  backup: 'резервное копирование',
  build: 'сборка',
  idle: 'простой',
  interactive: 'интерактивная работа',
  maintenance: 'обслуживание',
  unknown: 'неизвестно',
}

export const strengthNames: Record<string, string> = {
  characteristic: 'обязательный',
  typical: 'типичный',
  possible: 'возможный',
  contradicting: 'противоречащий',
}

// Effectiveness of an action and likelihood of a side effect are written in
// the words of strength (ADR 0035); read as how often.
export const likelihoodNames: Record<string, string> = {
  characteristic: 'почти всегда',
  typical: 'обычно',
  possible: 'иногда',
}

export const statusNames: Record<string, string> = {
  matched: 'найден',
  missing: 'нет',
  pending: 'ожидается',
  unobservable: 'не наблюдается',
  absent: 'не обнаружен',
}

export const detectorNames: Record<string, string> = {
  threshold: 'порог',
  'robust.mad': 'отклонение от нормы',
  'robust.ewma': 'дрейф от нормы (EWMA)',
  iforest: 'Isolation Forest',
  impact: 'влияние',
  growth: 'устойчивый рост',
}

export const kindNames: Record<string, string> = {
  anomaly: 'аномалия',
  impact: 'влияние',
}

export const levelNames: Record<string, string> = {
  noticeable: 'заметное',
  severe: 'сильное',
}

export const gradeNames: Record<string, string> = {
  documented: 'по документации',
  measured: 'измерено',
  operator: 'от оператора',
  unverified: 'не проверено',
}

export const collectorNames: Record<string, string> = {
  cpu: 'CPU',
  memory: 'Память',
  vmstat: 'vmstat',
  disk: 'Диски',
  fs: 'Файловые системы',
  pressure: 'PSI',
  cgroup: 'Control groups',
  processes: 'Процессы',
  thermal: 'Температура',
  power_supply: 'Питание',
  power_profile: 'Профиль питания',
  journal: 'Журнал',
  self: 'Сам агент',
}

export const collectorStates: Record<string, string> = {
  preferred: 'основной источник',
  fallback: 'запасной источник',
  disabled: 'отключён',
}

export const processStates: Record<string, string> = {
  R: 'выполняется',
  S: 'спит',
  D: 'ждёт I/O',
  I: 'простаивает',
  Z: 'зомби',
  T: 'остановлен',
}

export const directionNames: Record<string, string> = {
  up: 'рост',
  down: 'падение',
}

export const symptomKindNames: Record<string, string> = {
  anomaly: 'аномалия',
  value: 'значение',
  event: 'событие',
  process: 'процесс',
}

export function named(
  dict: Record<string, string>,
  key: string | null | undefined,
  fallback?: string | null,
) {
  if (!key) return '—'
  return dict[key] ?? fallback ?? key
}

export function detectorName(detector: string): string {
  return detectorNames[detector] ?? detectorNames[detector.split('.')[0]] ?? detector
}

/** A name of a signal, event type or process symptom, whichever it is. */
export function anyName(name: string): string {
  return signalNames[name] ?? eventNames[name] ?? processNames[name] ?? name
}

/** A symptom with alternatives (any one of them matches) in words; a long list reads as "one of". */
export function symptomNames(kind: string, names: string[]): string {
  const words = names.map((n) => symptomName(kind, n))
  if (words.length <= 2) return words.join(' или ')
  return `одно из: ${words.join('; ')}`
}

/** A symptom's names (one of them matches) in words. */
export function symptomName(kind: string, name: string): string {
  if (kind === 'event') return named(eventNames, name)
  if (kind === 'process') return named(processNames, name)
  return named(signalNames, name)
}
