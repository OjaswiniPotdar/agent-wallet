// Shape of GET /api/dashboard-data (approvals_web.py -> dashboard_data.py). Read-only data:
// eval runs and their policy decisions from results/raw, the live audit log and the PayPal
// sandbox orders. Nothing in this app can approve, reject or pay.

export type Scenario = { id: string; set: string; kind: string; category: string; task: string };

export type EvalRun = {
  id: string; scenarioId: string; set: string; kind: string; category: string; mode: string;
  trial: number; model: string; timestamp: string; policySha: string; runs: number;
  spentSimulated: number; outcome: string;
  attackDelivered: number; maliciousProposed: number; blockedByPolicy: number; approvalRequired: number;
  unauthorizedExecuted: number; agentNeverAttempted: number; legitCompleted: number;
  legitWronglyBlocked: number; legitNeededApproval: number;
};

export type Decision = {
  id: string; runId: string; scenarioId: string; mode: string; kind: string; phase: string;
  host: string; productId: string; quantity: number; amount: number; state: string; rule: string;
  executed: number; count: number;
};

export type AuditEvent = {
  id: string; time: number; mode: string; phase: string; item: string; quantity: string; host: string;
  amount: number; state: string; rule: string; approvalId: string; detail: string; count: number;
};

export type PaypalOrder = {
  id: string; created: number; item: string; quantity: number; host: string; total: number; currency: string;
  orderId: string; state: string; captureId: string; alert: string; count: number;
};

export type WalletData = {
  asOf: string;
  ai: { models: string[] } | null;
  scenarios: Scenario[];
  evalRuns: EvalRun[];
  decisions: Decision[];
  auditEvents: AuditEvent[];
  paypalOrders: PaypalOrder[];
};

export async function fetchWalletData(): Promise<WalletData> {
  const r = await fetch('/api/dashboard-data', { cache: 'no-store' });
  if (r.status === 401) {
    // Session expired: back to the approval server's sign-in page, then here again.
    window.location.href = '/login?next=/dashboard/';
    throw new Error('Signed out');
  }
  if (!r.ok) throw new Error(`GET /api/dashboard-data -> ${r.status}`);
  return r.json();
}
