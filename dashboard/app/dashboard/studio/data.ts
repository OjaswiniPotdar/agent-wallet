// AG Studio data sources for the Agent Wallet security dashboard: the eval runs and the policy
// decisions inside them, the scenarios they belong to, the live audit log and the PayPal sandbox
// orders, plus a few calculated measures. Field ids ('evalRuns.unauthorizedExecuted') are used
// by ./initial-report.ts and the custom widget in ./funnel-widget.tsx.

import type {
  AgDataSourcesDefinition,
  AgExpressionFieldDefinition,
  AgFieldDefinition,
  AgFormat,
  AgRelationDefinition,
} from 'ag-studio';
import type { WalletData } from '@/lib/wallet-data';

// [id, name, format, hide?]
type FieldSpec = [id: string, name: string, format: AgFormat, hide?: boolean];

const fields = (specs: FieldSpec[]): AgFieldDefinition[] =>
  specs.map(([id, name, format, hide]) => ({ id, name, format, ...(hide && { hide }) }));

const manyToOne = (id: string, source: string, target: string): AgRelationDefinition => {
  const [sourceTable, sourceField] = source.split('.');
  const [targetTable, targetField] = target.split('.');
  return {
    id,
    source: { tableId: sourceTable, fieldId: sourceField },
    target: { tableId: targetTable, fieldId: targetField },
    type: 'many-to-one',
  };
};

const sumOf = (id: string) => ({ id, aggregation: 'sum' as const });

const ratio = (id: string, name: string, description: string, numerator: string, denominator: string): AgExpressionFieldDefinition => ({
  id,
  name,
  description,
  format: 'percentageFormat',
  isMeasure: true,
  expression: { operator: 'divide', inputs: [sumOf(numerator), sumOf(denominator)] },
});

// One 0/1 flag per run, summed by widgets. Names say what the classifier measured.
const FLAG_FIELDS: FieldSpec[] = [
  ['attackDelivered', 'Attack page read', 'integerFormat'],
  ['maliciousProposed', 'Malicious purchase proposed', 'integerFormat'],
  ['blockedByPolicy', 'Blocked by code', 'integerFormat'],
  ['approvalRequired', 'Held for human approval', 'integerFormat'],
  ['unauthorizedExecuted', 'Unauthorized payment', 'integerFormat'],
  ['agentNeverAttempted', 'Model did not attempt', 'integerFormat'],
  ['legitCompleted', 'Legitimate task completed', 'integerFormat'],
  ['legitWronglyBlocked', 'Legitimate task wrongly blocked', 'integerFormat'],
  ['legitNeededApproval', 'Legitimate task needed approval', 'integerFormat'],
];

export const hasData = (data: WalletData) => data.evalRuns.length > 0 || data.auditEvents.length > 0;

export function buildStudioData(data: WalletData): AgDataSourcesDefinition {
  return {
    description:
      'Agent Wallet: a policy layer between an AI shopping agent (gpt-5-nano) and PayPal. The model only proposes purchases; ' +
      'deterministic code (allowlist, caps, budget, catalog price check, human approval) decides. ' +
      'evalRuns holds one row per evaluation run: 23 scenarios (13 attacks, 10 legitimate tasks) x 2 modes x 10 trials. ' +
      '"baseline" mode pays whatever the model proposes; "protected" mode goes through the policy engine. ' +
      'Flags are 0/1 and should be summed; "runs" is 1 per row. Payment capture in evals is SIMULATED. ' +
      '"Model did not attempt" means the model resisted on its own, which is not the same as being blocked by code. ' +
      'This is a small custom suite with one model: report raw counts, never a percentage "secure" claim.',
    sources: [
      {
        id: 'scenarios',
        description: 'The 23 evaluation scenarios. Attack scenarios embed a prompt injection or manipulation in the shop page.',
        data: data.scenarios,
        fields: fields([
          ['id', 'Scenario', 'textFormat'],
          ['set', 'Scenario Set', 'textFormat'],
          ['kind', 'Scenario Kind', 'textFormat'],
          ['category', 'Attack Category', 'textFormat'],
          ['task', 'User Task', 'textFormat'],
        ]),
      },
      {
        id: 'evalRuns',
        name: 'Eval Runs',
        description:
          'One row per evaluation run (460 rows). set is tuning or heldout (rules were tuned on tuning only). ' +
          'outcome is one mutually exclusive label per run, worst first.',
        data: data.evalRuns,
        fields: fields([
          ['id', 'Run ID', 'textFormat', true],
          ['scenarioId', 'Run Scenario', 'textFormat'],
          ['set', 'Set', 'textFormat'],
          ['kind', 'Kind', 'textFormat'],
          ['category', 'Category', 'textFormat'],
          ['mode', 'Mode', 'textFormat'],
          ['trial', 'Trial', 'integerFormat'],
          ['model', 'Model', 'textFormat'],
          ['timestamp', 'Run Time', 'dateTimeFormat', true],
          ['policySha', 'Policy Hash', 'textFormat', true],
          ['outcome', 'Outcome', 'textFormat'],
          ['runs', 'Runs', 'integerFormat'],
          ['spentSimulated', 'Spent (simulated)', 'currencyFormat'],
          ...FLAG_FIELDS,
        ]),
      },
      {
        id: 'decisions',
        name: 'Policy Decisions',
        description:
          'Every purchase the agent proposed during the evals and what the code did with it. ' +
          'phase agent_call is the proposal; after_human_approval is the retry after the (oracle) human decided. ' +
          'rule is the policy rule that decided (R1_ALLOWLIST, R3_CAP, R5_SPLIT, R6_APPROVAL, R7_DUPLICATE, OK, - for baseline).',
        data: data.decisions,
        fields: fields([
          ['id', 'Decision ID', 'textFormat', true],
          ['runId', 'Run ID', 'textFormat', true],
          ['scenarioId', 'Decision Scenario', 'textFormat', true],
          ['mode', 'Decision Mode', 'textFormat'],
          ['kind', 'Decision Kind', 'textFormat'],
          ['phase', 'Phase', 'textFormat'],
          ['host', 'Merchant Host', 'textFormat'],
          ['productId', 'Product', 'textFormat'],
          ['quantity', 'Quantity', 'integerFormat'],
          ['amount', 'Amount', 'currencyFormat'],
          ['state', 'Payment State', 'textFormat'],
          ['rule', 'Rule', 'textFormat'],
          ['executed', 'Executed', 'integerFormat'],
          ['count', 'Decisions', 'integerFormat'],
        ]),
      },
      {
        id: 'auditEvents',
        name: 'Audit Log',
        description: 'Live audit log of agent purchase decisions from local runs (SQLite). Payment capture is simulated unless state is CAPTURE_CONFIRMED.',
        data: data.auditEvents,
        fields: fields([
          ['id', 'Event ID', 'textFormat', true],
          ['time', 'Event Time', 'dateTimeFormat'],
          ['mode', 'Event Mode', 'textFormat'],
          ['phase', 'Event Phase', 'textFormat'],
          ['item', 'Item', 'textFormat'],
          ['quantity', 'Event Quantity', 'textFormat'],
          ['host', 'Event Host', 'textFormat'],
          ['amount', 'Event Amount', 'currencyFormat'],
          ['state', 'Event State', 'textFormat'],
          ['rule', 'Event Rule', 'textFormat'],
          ['approvalId', 'Approval', 'textFormat'],
          ['detail', 'Detail', 'textFormat'],
          ['count', 'Events', 'integerFormat'],
        ]),
      },
      {
        id: 'paypalOrders',
        name: 'PayPal Sandbox Orders',
        description:
          'Real PayPal sandbox orders created with --real-orders, reconciled against the approved intent. ' +
          'ORDER_CREATED is not a payment; CAPTURE_CONFIRMED only comes from PayPal itself.',
        data: data.paypalOrders,
        fields: fields([
          ['id', 'Intent ID', 'textFormat', true],
          ['created', 'Order Created', 'dateTimeFormat'],
          ['item', 'Order Item', 'textFormat'],
          ['quantity', 'Order Quantity', 'integerFormat'],
          ['host', 'Order Host', 'textFormat'],
          ['total', 'Order Total', 'currencyFormat'],
          ['currency', 'Order Currency', 'textFormat'],
          ['orderId', 'PayPal Order ID', 'textFormat'],
          ['state', 'Order State', 'textFormat'],
          ['captureId', 'Capture ID', 'textFormat'],
          ['alert', 'Reconciliation Alert', 'textFormat'],
          ['count', 'Orders', 'integerFormat'],
        ]),
      },
    ],
    relationships: [
      manyToOne('run-scenario', 'evalRuns.scenarioId', 'scenarios.id'),
      manyToOne('decision-run', 'decisions.runId', 'evalRuns.id'),
    ],
    expressions: [
      ratio('unauthorizedRate', 'Unauthorized Payment Rate', 'Unauthorized payments / runs.', 'evalRuns.unauthorizedExecuted', 'evalRuns.runs'),
      ratio('legitCompletionRate', 'Legitimate Completion Rate', 'Legitimate tasks completed / runs.', 'evalRuns.legitCompleted', 'evalRuns.runs'),
      ratio('proposalRate', 'Malicious Proposal Rate', 'Runs where the model proposed the malicious purchase / runs.', 'evalRuns.maliciousProposed', 'evalRuns.runs'),
    ],
  };
}
