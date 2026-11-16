import { costLabel, formatCost, type Cost } from './cost'

export function CostText({ cost }: { cost: Cost }) {
  return (
    <span className="num">
      <span aria-hidden="true">{formatCost(cost)}</span>
      <span className="sr-only">{costLabel(cost)}</span>
    </span>
  )
}
