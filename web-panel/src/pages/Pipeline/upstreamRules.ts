import type { TransferUpstreamGroup, TransferUpstreamOperator, TransferUpstreamRecordedRule } from '../../services/api';

export const oppositeOperator = (operator: TransferUpstreamOperator): TransferUpstreamOperator => operator === 'AND' ? 'OR' : 'AND';

export function upstreamExpression(
  operator: TransferUpstreamOperator,
  parents: { id: string; label: string }[],
  group?: TransferUpstreamGroup | null,
) {
  if (!group) return parents.length ? parents.map(parent => parent.label).join(` ${operator} `) : operator;
  const inside = parents.filter(parent => group.parentPlanIds.includes(parent.id));
  const outside = parents.filter(parent => !group.parentPlanIds.includes(parent.id));
  if (inside.length !== 2 || outside.length !== 1 || group.operator === operator) return 'Grouped condition unavailable';
  const inner = `(${inside.map(parent => parent.label).join(` ${group.operator} `)})`;
  return operator === 'OR' ? `${inner} OR ${outside[0].label}` : `${outside[0].label} AND ${inner}`;
}

export function recordedUpstreamRule(rule: TransferUpstreamRecordedRule) {
  if (!rule.group) return { expression: rule.operator, parents: [] };
  const parents = (rule.ruleParentPlanIds ?? []).map((id, index) => ({ id, label: 'ABC'[index] }));
  return { expression: upstreamExpression(rule.operator, parents, rule.group), parents };
}
