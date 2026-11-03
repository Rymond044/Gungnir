import dagre from '@dagrejs/dagre'
import { Background, Controls, Position, ReactFlow, type Edge, type Node } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import type { Knowledge, Strength } from '../api/types'
import { Chip, Key, Panel, Segmented } from '../components/ui'
import ui from '../components/ui.module.css'
import { DOMAINS, nameKeys } from '../lib/knowledge'
import { causeNames, domainNames, named, strengthNames, symptomKindNames, symptomName } from '../lib/names'

type Domain = (typeof DOMAINS)[number] | 'all'

const NODE_W = 230
const NODE_H = 48

const EDGE_STYLE: Record<Strength, React.CSSProperties> = {
  characteristic: { stroke: 'var(--blue)', strokeWidth: 2 },
  typical: { stroke: 'var(--ink)', strokeWidth: 1.2 },
  possible: { stroke: 'var(--gray)', strokeWidth: 1.2, strokeDasharray: '4 3' },
  contradicting: { stroke: 'var(--red)', strokeWidth: 1.4, strokeDasharray: '2 3' },
}

interface Built {
  nodes: Node[]
  edges: Edge[]
  height: number
}

/** Causes on the left, the signals, events and process symptoms they show through on the right. */
function build(k: Knowledge, domain: Domain): Built {
  const rules = k.rules.filter((r) => domain === 'all' || r.file === domain)
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 14, ranksep: 220, marginx: 10, marginy: 10 })
  g.setDefaultEdgeLabel(() => ({}))
  const symptoms = new Map<string, { kind: string; name: string }>()
  const edges: Edge[] = []
  for (const r of rules) {
    g.setNode(`cause:${r.cause}`, { width: NODE_W, height: NODE_H })
    for (const s of r.symptoms) {
      for (const key of nameKeys(s)) {
        const name = key.split(':')[1]
        symptoms.set(key, { kind: key.split(':')[0], name })
        const id = `${r.cause}->${key}`
        if (edges.some((e) => e.id === id)) continue
        g.setEdge(`cause:${r.cause}`, key)
        edges.push({
          id,
          source: `cause:${r.cause}`,
          target: key,
          data: { strength: s.strength, leading: s.leading },
          style: s.leading ? { ...EDGE_STYLE[s.strength], strokeWidth: 3.2 } : EDGE_STYLE[s.strength],
        })
      }
    }
  }
  for (const key of symptoms.keys()) g.setNode(key, { width: NODE_W, height: NODE_H })
  dagre.layout(g)

  const nodes: Node[] = [
    ...rules.map((r) => {
      const p = g.node(`cause:${r.cause}`)
      return {
        id: `cause:${r.cause}`,
        position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 },
        data: { label: named(causeNames, r.cause), kind: 'cause' },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        type: 'input',
        style: {
          width: NODE_W,
          height: NODE_H,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--side)',
          color: 'var(--side-text)',
          border: 0,
          borderRadius: 8,
          fontSize: 12.5,
        },
      } satisfies Node
    }),
    ...[...symptoms.entries()].map(([key, s]) => {
      const p = g.node(key)
      return {
        id: key,
        position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 },
        data: { label: symptomName(s.kind === 'anomaly' ? 'anomaly' : s.kind, s.name), kind: s.kind },
        targetPosition: Position.Left,
        type: 'output',
        style: {
          width: NODE_W,
          height: NODE_H,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background:
            s.kind === 'anomaly'
              ? 'var(--panel)'
              : s.kind === 'event'
                ? 'var(--ochre-tint)'
                : 'var(--blue-tint)',
          border: '1px solid var(--line)',
          borderRadius: 8,
          fontSize: 12.5,
          color: 'var(--text)',
        },
      } satisfies Node
    }),
  ]
  return { nodes, edges, height: g.graph().height ?? 600 }
}

export default function KnowledgeGraph({ k, host }: { k: Knowledge; host: string }) {
  const [domain, setDomain] = useState<Domain>('disk')
  const [selected, setSelected] = useState<string | null>(null)
  const built = useMemo(() => build(k, domain), [k, domain])

  const linked = useMemo(() => {
    if (!selected) return null
    const ids = new Set([selected])
    for (const e of built.edges) {
      if (e.source === selected) ids.add(e.target)
      if (e.target === selected) ids.add(e.source)
    }
    return ids
  }, [selected, built])

  const nodes = built.nodes.map((n) => ({
    ...n,
    style: { ...n.style, opacity: !linked || linked.has(n.id) ? 1 : 0.25 },
  }))
  const edges = built.edges.map((e) => ({
    ...e,
    style: { ...e.style, opacity: !selected || e.source === selected || e.target === selected ? 1 : 0.08 },
  }))

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
          }}
        />
        <span className="muted" style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', fontSize: 13 }}>
          {(Object.keys(EDGE_STYLE) as Strength[]).map((s) => (
            <span key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <svg width="22" height="6" aria-hidden="true">
                <line x1="0" y1="3" x2="22" y2="3" style={EDGE_STYLE[s]} />
              </svg>
              {strengthNames[s]}
            </span>
          ))}
          <span>толстая линия — ведущий симптом</span>
        </span>
      </div>
      <div className={ui.row}>
        <div
          style={{
            flex: '3 1 560px',
            height: Math.max(480, built.height + 40),
            background: 'var(--panel)',
            border: '1px solid var(--line)',
            borderRadius: 10,
          }}
        >
          <ReactFlow
            key={domain}
            nodes={nodes}
            edges={edges}
            // Full size: the page scrolls through a tall graph instead of shrinking it.
            defaultViewport={{ x: 20, y: 10, zoom: 1 }}
            minZoom={0.2}
            nodesDraggable={false}
            nodesConnectable={false}
            onNodeClick={(_, n) => setSelected(n.id === selected ? null : n.id)}
            onPaneClick={() => setSelected(null)}
            proOptions={{ hideAttribution: true }}
            // The page scrolls; the wheel over the graph does not zoom it.
            zoomOnScroll={false}
            preventScrolling={false}
          >
            <Background gap={20} color="var(--grid-minor)" />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <div style={{ flex: '1 1 260px', minWidth: 0 }}>
          <Selected k={k} id={selected} host={host} edges={built.edges} />
        </div>
      </div>
    </div>
  )
}

function Selected({ k, id, host, edges }: { k: Knowledge; id: string | null; host: string; edges: Edge[] }) {
  if (!id) {
    return (
      <Panel title="Как читать">
        <span>
          Слева причины, справа то, в чём они проявляются: метрики, события ядра и журнала, состояния
          процессов. Нажмите на узел, чтобы оставить только его связи.
        </span>
      </Panel>
    )
  }
  if (id.startsWith('cause:')) {
    const cause = id.slice(6)
    const info = k.causes.find((c) => c.name === cause)
    return (
      <Panel title={named(causeNames, cause)}>
        <Key>{cause}</Key>
        <span>{info?.comment}</span>
        <span className="muted">{edges.filter((e) => e.source === id).length} связей с симптомами</span>
        <Link to={`/hosts/${host}/knowledge/${cause}`} className={`${ui.button} ${ui.primary}`}>
          Открыть правило
        </Link>
      </Panel>
    )
  }
  const [kind, name] = id.split(':')
  const users = edges.filter((e) => e.target === id)
  return (
    <Panel title={symptomName(kind, name)}>
      <span>
        <Chip>{symptomKindNames[kind]}</Chip> <Key>{name}</Key>
      </span>
      <span className="muted">Используют {users.length} причин:</span>
      {users.map((e) => {
        const cause = e.source.slice(6)
        const strength = (e.data as { strength: Strength }).strength
        return (
          <span key={e.id}>
            <Link to={`/hosts/${host}/knowledge/${cause}`}>{named(causeNames, cause)}</Link>{' '}
            <span className="muted">· {strengthNames[strength]}</span>
          </span>
        )
      })}
    </Panel>
  )
}
