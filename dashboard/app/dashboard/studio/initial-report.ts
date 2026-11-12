// The built-in "Agent Wallet security" report, as AG Studio report state. Field ids
// ('evalRuns.unauthorizedExecuted') refer to the data sources in ./data.ts; 'defense-funnel' is the
// custom widget in ./funnel-widget.tsx. Every number is a raw count from the eval results.

import type { AgPageState, AgReportState, AgWidgetLayoutState } from 'ag-studio';

// Position on the canvas grid, in tracks: 24 columns wide, rows of studioCanvasRowHeight.
const at = (xTrack: number, yTrack: number, xSpan: number, ySpan: number): AgWidgetLayoutState => ({
  xTrack,
  yTrack,
  xSpan,
  ySpan,
});
const title = (text: string) => ({ title: { enabled: true, text } });
const titled = (text: string, sub: string) => ({
  title: { enabled: true, text },
  subtitle: { enabled: true, text: sub },
});
const sum = (id: string) => ({ id, aggregation: 'sum' as const });
const field = (id: string) => ({ id });

const FONT = 'var(--pp-font)';
const typography = (fontSize: number, fontWeight: 'normal' | 'bold' = 'normal') => ({
  fontFamily: FONT,
  fontSize,
  fontWeight,
  fontStyle: 'normal' as const,
});

const kpi = (text: string, caption: string, value: { id: string; aggregation?: 'sum' }) => ({
  type: 'value' as const,
  dataMapping: { value: [value] },
  format: {
    title: { enabled: true, text, typography: typography(13, 'bold') },
    caption: { enabled: true, text: caption, typography: typography(12) },
    style: { typography: typography(30, 'bold') },
  },
});

const only = (fieldId: string, ...values: string[]) => [
  { field: { id: fieldId }, model: { operator: 'isIn' as const, value: values } },
];
const legend = { style: { theme: { common: { legend: { enabled: true } } } } };

const funnel = (text: string, sub: string) => ({
  type: 'defense-funnel' as const,
  dataMapping: {
    stages: [
      sum('evalRuns.runs'),
      sum('evalRuns.attackDelivered'),
      sum('evalRuns.maliciousProposed'),
      sum('evalRuns.unauthorizedExecuted'),
    ],
  },
  format: titled(text, sub),
  // A custom widget type: Studio's default-widget typing does not know it, so it is cast.
}) as never;

const setFilter = {
  type: 'button-filter' as const,
  dataMapping: { value: [field('evalRuns.set')] },
  format: { ...title('Scenario set'), style: { dimensions: { height: 28, minWidth: 64 } } },
};

// --- Pages ------------------------------------------------------------------------

const attacks: AgPageState = {
  id: 'attacks',
  widgets: {
    'kpi-runs': kpi('Attack runs', 'both modes, 10 trials per scenario', sum('evalRuns.runs')),
    'kpi-base': kpi('Paid attacker: baseline', 'unauthorized payments, no guard', sum('evalRuns.unauthorizedExecuted')),
    'kpi-prot': kpi('Paid attacker: protected', 'unauthorized payments, code enforces', sum('evalRuns.unauthorizedExecuted')),
    'kpi-proposed': kpi('Model still tried (protected)', 'malicious purchase proposed', sum('evalRuns.maliciousProposed')),
    set: setFilter,
    'funnel-base': funnel('Baseline: model proposes, nothing checks', 'Attack runs → page read → malicious purchase proposed → paid'),
    'funnel-prot': funnel('Protected: model proposes, code enforces', 'Same model, prompt, tools and scenarios'),
    outcomes: {
      type: 'column-chart-stacked',
      dataMapping: {
        categoryKey: [field('evalRuns.mode')],
        valueKey: [sum('evalRuns.runs')],
        legendKey: [field('evalRuns.outcome')],
      },
      format: { ...titled('How each attack run ended', 'One label per run, worst outcome first'), ...legend },
    },
    'by-category': {
      type: 'bar-chart-stacked',
      dataMapping: {
        categoryKey: [field('evalRuns.category')],
        valueKey: [sum('evalRuns.runs')],
        legendKey: [field('evalRuns.outcome')],
      },
      format: { ...titled('Protected mode: where each attack stopped', 'By attack category'), ...legend },
    },
    'scenario-pivot': {
      type: 'pivot-grid',
      dataMapping: {
        rows: [field('evalRuns.category'), field('evalRuns.scenarioId')],
        columns: [field('evalRuns.mode')],
        values: [sum('evalRuns.unauthorizedExecuted'), sum('evalRuns.blockedByPolicy'), sum('evalRuns.approvalRequired'), sum('evalRuns.agentNeverAttempted')],
      },
      format: { ...title('Per scenario (10 trials per cell)'), style: { grandTotalRow: { enabled: true } } },
    },
  },
  widgetLayout: {
    'kpi-runs': at(0, 0, 5, 7),
    'kpi-base': at(5, 0, 5, 7),
    'kpi-prot': at(10, 0, 5, 7),
    'kpi-proposed': at(15, 0, 5, 7),
    set: at(20, 0, 4, 7),
    'funnel-base': at(0, 7, 12, 16),
    'funnel-prot': at(12, 7, 12, 16),
    outcomes: at(0, 23, 10, 20),
    'by-category': at(10, 23, 14, 20),
    'scenario-pivot': at(0, 43, 24, 26),
  },
  filter: {
    page: only('evalRuns.kind', 'attack'),
    widget: {
      'kpi-base': only('evalRuns.mode', 'baseline'),
      'kpi-prot': only('evalRuns.mode', 'protected'),
      'kpi-proposed': only('evalRuns.mode', 'protected'),
      'funnel-base': only('evalRuns.mode', 'baseline'),
      'funnel-prot': only('evalRuns.mode', 'protected'),
      'by-category': only('evalRuns.mode', 'protected'),
    },
  },
};

const legit: AgPageState = {
  id: 'legit',
  widgets: {
    'kpi-runs': kpi('Legitimate-task runs', 'both modes, 10 trials per scenario', sum('evalRuns.runs')),
    'kpi-done': kpi('Completed: protected', 'the guard must not get in the way', sum('evalRuns.legitCompleted')),
    'kpi-wrong': kpi('Wrongly blocked: protected', 'false positives', sum('evalRuns.legitWronglyBlocked')),
    'kpi-approval': kpi('Approval needed: protected', 'chair / whiteboard above $75', sum('evalRuns.legitNeededApproval')),
    set: setFilter,
    outcomes: {
      type: 'column-chart-stacked',
      dataMapping: {
        categoryKey: [field('evalRuns.mode')],
        valueKey: [sum('evalRuns.runs')],
        legendKey: [field('evalRuns.outcome')],
      },
      format: { ...titled('How each legitimate run ended', 'Baseline "extra payment" = the model bought from a host it invented'), ...legend },
    },
    'by-scenario': {
      type: 'pivot-grid',
      dataMapping: {
        rows: [field('evalRuns.scenarioId')],
        columns: [field('evalRuns.mode')],
        values: [sum('evalRuns.legitCompleted'), sum('evalRuns.legitNeededApproval'), sum('evalRuns.legitWronglyBlocked'), sum('evalRuns.unauthorizedExecuted')],
      },
      format: { ...title('Per scenario (10 trials per cell)'), style: { grandTotalRow: { enabled: true } } },
    },
  },
  widgetLayout: {
    'kpi-runs': at(0, 0, 5, 7),
    'kpi-done': at(5, 0, 5, 7),
    'kpi-wrong': at(10, 0, 5, 7),
    'kpi-approval': at(15, 0, 5, 7),
    set: at(20, 0, 4, 7),
    outcomes: at(0, 7, 9, 22),
    'by-scenario': at(9, 7, 15, 22),
  },
  filter: {
    page: only('evalRuns.kind', 'legit'),
    widget: {
      'kpi-done': only('evalRuns.mode', 'protected'),
      'kpi-wrong': only('evalRuns.mode', 'protected'),
      'kpi-approval': only('evalRuns.mode', 'protected'),
    },
  },
};

const decisions: AgPageState = {
  id: 'decisions',
  widgets: {
    'kpi-decisions': kpi('Purchase proposals (protected)', 'every purchase tool call the model made', sum('decisions.count')),
    'kpi-executed': kpi('Executed (protected)', 'passed every check, capture simulated', sum('decisions.executed')),
    rules: {
      type: 'donut-chart',
      dataMapping: { categoryKey: [field('decisions.rule')], valueKey: [sum('decisions.count')] },
      format: {
        ...titled('Which rule decided', 'Protected mode, first proposal of each purchase'),
        style: {
          custom: { pieDataLabel: { enabled: true }, totalLabel: { enabled: true } },
          theme: { common: { legend: { enabled: true, position: 'right' } } },
        },
      },
    },
    states: {
      type: 'bar-chart-stacked',
      dataMapping: {
        categoryKey: [field('decisions.state')],
        valueKey: [sum('decisions.count')],
        legendKey: [field('decisions.kind')],
      },
      format: { ...titled('Payment state after the code decided', 'Attack vs legitimate scenarios'), ...legend },
    },
    hosts: {
      type: 'grid',
      dataMapping: {
        cols: [field('decisions.host'), field('decisions.rule'), field('decisions.state'), sum('decisions.count'), sum('decisions.executed')],
      },
      sort: [{ field: sum('decisions.count'), direction: 'desc' }],
      format: titled('Merchant hosts the model tried to pay', 'Look-alike and redirect hosts are blocked by R1'),
    },
    'decision-grid': {
      type: 'grid',
      dataMapping: {
        cols: [
          field('decisions.scenarioId'),
          field('decisions.phase'),
          field('decisions.host'),
          field('decisions.productId'),
          field('decisions.quantity'),
          field('decisions.amount'),
          field('decisions.state'),
          field('decisions.rule'),
          sum('decisions.count'),
        ],
      },
      format: titled('Every protected-mode decision', 'Identical decisions across the 10 trials are grouped; Decisions = how many times'),
    },
  },
  widgetLayout: {
    'kpi-decisions': at(0, 0, 6, 7),
    'kpi-executed': at(6, 0, 6, 7),
    rules: at(0, 7, 13, 22),
    states: at(13, 7, 11, 22),
    hosts: at(0, 53, 24, 16),
    'decision-grid': at(0, 29, 24, 24),
  },
  filter: {
    page: only('decisions.mode', 'protected'),
    widget: { rules: only('decisions.phase', 'agent_call') },
  },
};

const live: AgPageState = {
  id: 'live',
  widgets: {
    'kpi-events': kpi('Audit events', 'from local agent runs (audit.db)', sum('auditEvents.count')),
    'kpi-orders': kpi('PayPal sandbox orders', 'real orders from --real-orders', sum('paypalOrders.count')),
    note: {
      type: 'text',
      dataMapping: {},
      format: {
        style: {
          text:
            'Audit log and PayPal orders are live from this machine. Click the refresh button above after a run. ' +
            'ORDER_CREATED is not a payment; CAPTURE_CONFIRMED only ever comes from PayPal. Eval and demo captures are SIMULATED.',
          typography: typography(13),
          textAlign: 'left',
        },
      },
    },
    audit: {
      type: 'grid',
      dataMapping: {
        cols: [
          field('auditEvents.time'),
          field('auditEvents.mode'),
          field('auditEvents.phase'),
          field('auditEvents.item'),
          field('auditEvents.quantity'),
          field('auditEvents.host'),
          sum('auditEvents.amount'),
          field('auditEvents.state'),
          field('auditEvents.rule'),
          field('auditEvents.approvalId'),
        ],
      },
      sort: [{ field: field('auditEvents.time'), direction: 'desc' }],
      format: title('Audit log, newest first'),
    },
    orders: {
      type: 'grid',
      dataMapping: {
        cols: [
          field('paypalOrders.created'),
          field('paypalOrders.item'),
          field('paypalOrders.quantity'),
          sum('paypalOrders.total'),
          field('paypalOrders.currency'),
          field('paypalOrders.orderId'),
          field('paypalOrders.state'),
          field('paypalOrders.alert'),
        ],
      },
      sort: [{ field: field('paypalOrders.created'), direction: 'desc' }],
      format: titled('PayPal sandbox orders', 'Reconciled against the approved intent (custom_id, amount, currency)'),
    },
  },
  widgetLayout: {
    'kpi-events': at(0, 0, 6, 7),
    'kpi-orders': at(6, 0, 6, 7),
    note: at(12, 0, 12, 7),
    audit: at(0, 7, 24, 24),
    orders: at(0, 31, 24, 16),
  },
};

export const dashboardReport: AgReportState = {
  selectedPageId: attacks.id,
  pages: [attacks, legit, decisions, live],
};
