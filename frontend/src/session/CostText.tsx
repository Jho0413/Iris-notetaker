import { costLabel, formatCost, type Cost } from './cost'

/** A cost in tabular numerals, read out in words ("at least $0.89, …", "less than 1 cent"). */
export function CostText({ cost }: { cost: Cost }) {
  return (
    <span className="num">
      <span aria-hidden="true">{formatCost(cost)}</span>
      <span className="sr-only">{costLabel(cost)}</span>
    </span>
  )
}
