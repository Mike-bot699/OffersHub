// Charts follow the house rules: one axis, thin marks, recessive grid, tokens for colour,
// hover tooltips on every chart, and a single series needs no legend (the title names it).
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { money, num } from './format'

const axis = { stroke: 'var(--text-3)', fontSize: 11, tickLine: false, axisLine: false } as const

function Tip({ active, payload, label, fmt, name }: {
  active?: boolean; payload?: { value: number }[]; label?: string; fmt: (v: number) => string; name: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="chart-tooltip">
      <div className="muted">{label}</div>
      <div><strong>{fmt(payload[0].value)}</strong> {name}</div>
    </div>
  )
}

export function TrendLine({ data, x, y, name, currency }: {
  data: Record<string, unknown>[]; x: string; y: string; name: string; currency?: string
}) {
  const fmt = (v: number) => (currency ? money(v, currency) : num(v))
  return (
    <div className="chart" role="img" aria-label={`${name} over time`}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey={x} {...axis} minTickGap={28} tickFormatter={(v: string) => v.slice(5)} />
          <YAxis {...axis} width={56} tickFormatter={(v: number) => (currency ? money(v, currency) : num(v))} />
          <Tooltip content={<Tip fmt={fmt} name={name} />} cursor={{ stroke: 'var(--border-strong)' }} />
          <Line type="monotone" dataKey={y} stroke="var(--series-1)" strokeWidth={2} dot={false}
            activeDot={{ r: 4, stroke: 'var(--surface)', strokeWidth: 2 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export function Bars({ data, x, y, name, fmtX, highlight }: {
  data: Record<string, unknown>[]; x: string; y: string; name: string; fmtX?: (v: unknown) => string
  highlight?: (row: Record<string, unknown>) => boolean
}) {
  const rows = data.map((d) => ({ ...d, __label: fmtX ? fmtX(d[x]) : String(d[x]) }))
  return (
    <div className="chart" role="img" aria-label={name}>
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap={2}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="__label" {...axis} interval="preserveStartEnd" minTickGap={8} />
          <YAxis {...axis} width={44} />
          <Tooltip content={<Tip fmt={(v) => num(v)} name={name} />} cursor={{ fill: 'var(--surface-2)' }} />
          <Bar dataKey={y} radius={[4, 4, 0, 0]} isAnimationActive={false}
            shape={(props: unknown) => {
              const p = props as { x: number; y: number; width: number; height: number; payload: Record<string, unknown> }
              const fill = highlight && highlight(p.payload) ? 'var(--series-2)' : 'var(--series-1)'
              const r = Math.min(4, p.width / 2, p.height)
              const { x: bx, y: by, width: w, height: h } = p
              if (h <= 0) return <g />
              return <path fill={fill} d={`M${bx},${by + h} V${by + r} Q${bx},${by} ${bx + r},${by} H${bx + w - r} Q${bx + w},${by} ${bx + w},${by + r} V${by + h} Z`} />
            }} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
