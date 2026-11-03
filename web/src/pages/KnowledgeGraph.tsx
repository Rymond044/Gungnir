import { useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from 'react-force-graph-2d'
import { Link } from 'react-router'
import type { Knowledge, Strength } from '../api/types'
import { Chip, Key, Panel, Segmented } from '../components/ui'
import ui from '../components/ui.module.css'
import { DOMAINS, nameKeys } from '../lib/knowledge'
import { causeNames, domainNames, named, strengthNames, symptomKindNames, symptomName } from '../lib/names'

type Domain = (typeof DOMAINS)[number] | 'all'
type Kind = 'cause' | 'anomaly' | 'event' | 'process'

// The canvas cannot read CSS variables; these are the colours of palette D
// (styles/tokens.css).
const COLOR: Record<Kind, string> = {
  cause: '#262422',
  anomaly: '#2f6db5',
  event: '#a86400',
  process: '#3c7d55',
}
const EDGE: Record<Strength, { color: string; dash: number[] | null }> = {
  characteristic: { color: '#2f6db5', dash: null },
  typical: { color: '#8a857c', dash: null },
  possible: { color: '#b9b3a9', dash: [3, 3] },
  contradicting: { color: '#b8352b', dash: [2, 3] },
}
const KIND_NAMES: Record<Kind, string> = {
  cause: 'причина',
  anomaly: 'метрика',
  event: 'событие ядра или журнала',
  process: 'симптом процесса',
}
const FAINT = 'rgba(115, 109, 100, 0.12)'
const HALO = 'rgba(245, 244, 241, 0.85)'
const HEIGHT = 640

interface GNode {
  id: string
  kind: Kind
  name: string
  label: string
  degree: number
}

interface GLink {
  source: string
  target: string
  strength: Strength
  leading: boolean
}

function build(k: Knowledge, domain: Domain): { nodes: GNode[]; links: GLink[] } {
  const rules = k.rules.filter((r) => domain === 'all' || r.file === domain)
  const nodes = new Map<string, GNode>()
  const links: GLink[] = []
  for (const r of rules) {
    const id = `cause:${r.cause}`
    nodes.set(id, { id, kind: 'cause', name: r.cause, label: named(causeNames, r.cause), degree: 0 })
    for (const s of r.symptoms) {
      for (const key of nameKeys(s)) {
        const [kind, name] = key.split(':') as [Kind, string]
        if (!nodes.has(key))
          nodes.set(key, { id: key, kind, name, label: symptomName(kind, name), degree: 0 })
        if (links.some((l) => l.source === id && l.target === key)) continue
        links.push({ source: id, target: key, strength: s.strength, leading: s.leading })
        nodes.get(id)!.degree += 1
        nodes.get(key)!.degree += 1
      }
    }
  }
  return { nodes: [...nodes.values()], links }
}

// The simulation replaces a link's ends by the node objects.
function endId(end: string | number | NodeObject | undefined): string {
  return typeof end === 'object' ? String(end.id) : String(end)
}

function radius(n: GNode): number {
  return n.kind === 'cause' ? 7 : 3.5 + Math.sqrt(n.degree) * 1.4
}

/**
 * The knowledge base as a living graph: causes and what they show through,
 * pulled together by their links. Hovering a node lights up its neighbours;
 * clicking keeps them lit and lets the links run.
 */
export default function KnowledgeGraph({ k, host }: { k: Knowledge; host: string }) {
  const [domain, setDomain] = useState<Domain>('disk')
  const [selected, setSelected] = useState<string | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const graph = useRef<ForceGraphMethods<NodeObject<GNode>, LinkObject<GNode, GLink>>>(undefined)
  const [width, setWidth] = useState(800)
  const data = useMemo(() => build(k, domain), [k, domain])
  // The graph stays hidden while it lays itself out and is shown once,
  // already fitted to its box: otherwise the operator watches it settle and
  // then jump to a new scale. Dragging a node later reheats the simulation
  // without fitting again.
  const [ready, setReady] = useState(false)
  // Label boxes drawn in this frame, in graph units: a label that would cover
  // another moves above its node, or waits for hover or zoom.
  const placed = useRef<[number, number, number, number][]>([])

  useEffect(() => {
    const el = box.current
    if (!el) return
    const observer = new ResizeObserver(() => setWidth(el.clientWidth))
    observer.observe(el)
    setWidth(el.clientWidth)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    // Spread the nodes a little more than the defaults, so labels fit.
    graph.current?.d3Force('charge')?.strength(-170)
    graph.current?.d3Force('link')?.distance(70)
    graph.current?.d3ReheatSimulation()
  }, [data])

  const focus = hovered ?? selected
  const lit = useMemo(() => {
    if (!focus) return null
    const ids = new Set([focus])
    for (const l of data.links) {
      const a = endId(l.source)
      const b = endId(l.target)
      if (a === focus) ids.add(b)
      if (b === focus) ids.add(a)
    }
    return ids
  }, [focus, data])

  const touches = (l: LinkObject<GNode, GLink>) =>
    !!focus && (endId(l.source) === focus || endId(l.target) === focus)

  return (
    <div className={ui.page}>
      <div className={ui.head}>
        <Segmented<Domain>
          label="Домен"
          options={[
            ...DOMAINS.map((d) => ({ value: d as Domain, label: domainNames[d] })),
            { value: 'all', label: 'Все' },
          ]}
          value={domain}
          onChange={(d) => {
            setDomain(d)
            setSelected(null)
            setReady(false)
          }}
        />
        <span className="muted" style={{ fontSize: 13 }}>
          Ctrl + колесо — масштаб, перетаскивание — сдвиг и узлы, клик — закрепить подсветку
        </span>
      </div>
      <Legend />
      <div className={ui.row}>
        <div
          ref={box}
          style={{
            flex: '3 1 560px',
            minWidth: 0,
            height: HEIGHT,
            background: 'var(--panel)',
            border: '1px solid var(--line)',
            borderRadius: 10,
            overflow: 'hidden',
            position: 'relative',
          }}
        >
          {!ready && <span className={ui.overlay}>Раскладываю граф…</span>}
          <div style={{ opacity: ready ? 1 : 0, transition: 'opacity 0.2s' }}>
            <ForceGraph2D<GNode, GLink>
              ref={graph}
              graphData={data}
              width={width}
              height={HEIGHT}
              backgroundColor="#f5f4f1"
              warmupTicks={150}
              cooldownTicks={150}
              onEngineStop={() => {
                if (ready) return
                graph.current?.zoomToFit(0, 40)
                setReady(true)
              }}
              // The page scrolls under the wheel; the graph zooms with Ctrl, as
              // maps do, and with a trackpad pinch, which arrives as Ctrl + wheel.
              enableZoomInteraction={(e: MouseEvent) => e.ctrlKey || e.metaKey}
              onRenderFramePre={() => {
                placed.current = []
              }}
              nodeRelSize={1}
              nodeVal={(n) => radius(n) ** 2}
              nodeLabel={(n) => `${n.label} (${KIND_NAMES[n.kind]})`}
              nodeCanvasObject={(n, ctx, scale) => {
                const r = radius(n)
                const dim = !!lit && !lit.has(n.id)
                ctx.beginPath()
                ctx.arc(n.x!, n.y!, r, 0, 2 * Math.PI)
                ctx.fillStyle = dim ? FAINT : COLOR[n.kind]
                ctx.fill()
                if (n.id === focus) {
                  ctx.lineWidth = 2 / scale
                  ctx.strokeStyle = '#a86400'
                  ctx.stroke()
                }
                // Labels of causes always; of the rest when zoomed in or lit.
                const show = n.kind === 'cause' || scale > 1.6 || (!!lit && lit.has(n.id))
                if (!show || dim) return
                const size = Math.max(10 / scale, n.kind === 'cause' ? 4.2 : 3.4)
                ctx.font = `${n.kind === 'cause' ? 600 : 400} ${size}px Golos Text, sans-serif`
                const w = ctx.measureText(n.label).width
                const pad = size * 0.2
                const box = (top: number): [number, number, number, number] => [
                  n.x! - w / 2 - pad,
                  top - pad,
                  n.x! + w / 2 + pad,
                  top + size + pad,
                ]
                const free = (b: [number, number, number, number]) =>
                  !placed.current.some((p) => b[0] < p[2] && p[0] < b[2] && b[1] < p[3] && p[1] < b[3])
                const below = box(n.y! + r + 1.5)
                const above = box(n.y! - r - 1.5 - size)
                const spot = free(below) ? below : free(above) ? above : n.id === focus ? below : null
                if (!spot) return
                placed.current.push(spot)
                ctx.fillStyle = HALO
                ctx.fillRect(spot[0], spot[1], spot[2] - spot[0], spot[3] - spot[1])
                ctx.textAlign = 'center'
                ctx.textBaseline = 'top'
                ctx.fillStyle = n.kind === 'cause' ? '#1c1b19' : '#4a463f'
                ctx.fillText(n.label, n.x!, spot[1] + pad)
              }}
              nodePointerAreaPaint={(n, color, ctx) => {
                ctx.beginPath()
                ctx.arc(n.x!, n.y!, radius(n) + 2, 0, 2 * Math.PI)
                ctx.fillStyle = color
                ctx.fill()
              }}
              linkColor={(l) => (lit && !touches(l) ? FAINT : EDGE[l.strength].color)}
              linkWidth={(l) => (l.leading ? 2.4 : 1) * (touches(l) ? 1.5 : 1)}
              linkLineDash={(l) => EDGE[l.strength].dash}
              linkDirectionalParticles={(l) => (touches(l) ? 3 : 0)}
              linkDirectionalParticleWidth={2.5}
              linkDirectionalParticleSpeed={0.008}
              linkDirectionalParticleColor={(l) => EDGE[l.strength].color}
              onNodeHover={(n) => setHovered(n ? String(n.id) : null)}
              onNodeClick={(n) => setSelected(String(n.id) === selected ? null : String(n.id))}
              onBackgroundClick={() => setSelected(null)}
            />
          </div>
        </div>
        <div style={{ flex: '1 1 260px', minWidth: 0 }}>
          <Selected k={k} id={selected} host={host} links={data.links} />
        </div>
      </div>
    </div>
  )
}

function Legend() {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 18px', fontSize: 13, color: 'var(--muted)' }}>
      {(Object.keys(COLOR) as Kind[]).map((kind) => (
        <span key={kind} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <svg width={kind === 'cause' ? 14 : 10} height={kind === 'cause' ? 14 : 10} aria-hidden="true">
            <circle cx="50%" cy="50%" r="45%" fill={COLOR[kind]} />
          </svg>
          {KIND_NAMES[kind]}
        </span>
      ))}
      <span style={{ width: 1, background: 'var(--line)' }} />
      {(Object.keys(EDGE) as Strength[]).map((s) => (
        <span key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <svg width="24" height="6" aria-hidden="true">
            <line
              x1="0"
              y1="3"
              x2="24"
              y2="3"
              stroke={EDGE[s].color}
              strokeWidth="1.6"
              strokeDasharray={EDGE[s].dash?.join(' ')}
            />
          </svg>
          {strengthNames[s]}
        </span>
      ))}
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <svg width="24" height="6" aria-hidden="true">
          <line x1="0" y1="3" x2="24" y2="3" stroke="#8a857c" strokeWidth="3" />
        </svg>
        ведущий симптом
      </span>
      <span>размер узла — сколько у него связей</span>
    </div>
  )
}

function Selected({ k, id, host, links }: { k: Knowledge; id: string | null; host: string; links: GLink[] }) {
  if (!id) {
    return (
      <Panel title="Как читать">
        <span>
          Тёмные узлы — причины, цветные — то, в чём они проявляются: метрики, события ядра и журнала,
          состояния процессов. Линия от причины к симптому значит, что правило ожидает этот симптом; цвет и
          пунктир говорят, насколько он обязателен. Наведите на узел, чтобы увидеть его связи, нажмите, чтобы
          закрепить.
        </span>
      </Panel>
    )
  }
  const touching = links.filter((l) => endId(l.source) === id || endId(l.target) === id)
  if (id.startsWith('cause:')) {
    const cause = id.slice(6)
    const info = k.causes.find((c) => c.name === cause)
    return (
      <Panel title={named(causeNames, cause)}>
        <span>
          <Chip>{KIND_NAMES.cause}</Chip> <Key>{cause}</Key>
        </span>
        <span>{info?.comment}</span>
        <span className="muted">{touching.length} симптомов в правиле</span>
        <Link to={`/hosts/${host}/knowledge/${cause}`} className={`${ui.button} ${ui.primary}`}>
          Открыть правило
        </Link>
      </Panel>
    )
  }
  const [kind, name] = id.split(':') as [Kind, string]
  return (
    <Panel title={symptomName(kind, name)}>
      <span>
        <Chip>{symptomKindNames[kind] ?? KIND_NAMES[kind]}</Chip> <Key>{name}</Key>
      </span>
      <span className="muted">Встречается в правилах {touching.length} причин:</span>
      {touching.map((l) => {
        const cause = endId(l.source).slice(6)
        return (
          <span key={cause}>
            <Link to={`/hosts/${host}/knowledge/${cause}`}>{named(causeNames, cause)}</Link>{' '}
            <span className="muted">
              · {strengthNames[l.strength]}
              {l.leading ? ', ведущий' : ''}
            </span>
          </span>
        )
      })}
    </Panel>
  )
}
