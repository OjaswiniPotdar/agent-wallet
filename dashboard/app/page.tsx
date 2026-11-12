'use client';

// Loads the data from the local approvals server (same origin) and renders the dashboard.
import { useCallback, useEffect, useState } from 'react';
import { fetchWalletData, type WalletData } from '@/lib/wallet-data';
import { Dashboard } from './dashboard/dashboard';
import { DashboardSkeleton } from './dashboard/components/skeleton';

export default function Home() {
  const [data, setData] = useState<WalletData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setData(await fetchWalletData());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  if (error && !data)
    return (
      <main className="m-auto max-w-lg p-10 text-center font-paypal text-pp-ink">
        <h1 className="text-xl font-semibold text-pp-navy">Could not load dashboard data</h1>
        <p className="mt-2 text-pp-muted">{error}</p>
        <p className="mt-2 text-pp-muted">
          Open this page from the approvals server: <code>python scripts/dev.py approvals</code>, then{' '}
          <code>http://localhost:8002/dashboard/</code>.
        </p>
      </main>
    );
  if (!data) return <DashboardSkeleton />;
  return (
    <Dashboard
      data={data}
      licenseKey={process.env.NEXT_PUBLIC_AG_STUDIO_LICENSE || undefined}
      aiModels={data.ai?.models}
      onRefresh={load}
    />
  );
}
