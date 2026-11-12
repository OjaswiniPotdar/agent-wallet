'use client';

import type { ReactNode } from 'react';
import { BellIcon, HelpIcon } from './ui/icons';
import { AgStudioLogo } from './ui/logo';

// The app frame (from the PayPal x AG Studio starter), laid out like the PayPal Developer Dashboard: a white top bar, a left sidebar
// and a light canvas. Shared by the dashboard, its loading skeleton and its error page.

function PayPalWordmark({ className = '' }: { className?: string }) {
  return (
    <span className={`font-bold italic tracking-tight ${className}`}>
      <span className="text-pp-blue-dark">Pay</span>
      <span className="text-pp-sky">Pal</span>
    </span>
  );
}

function ShieldIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-7" aria-hidden>
      <path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3Z" fill="var(--pp-navy)" />
      <path d="m8.5 12.2 2.4 2.4 4.8-5" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function BrandLockup() {
  return (
    <div className="flex items-center gap-3">
      <span className="flex items-center gap-2">
        <ShieldIcon />
        <span className="text-lg font-semibold text-pp-navy">Agent Wallet</span>
        <span className="hidden text-sm text-pp-muted md:inline">security dashboard</span>
      </span>
      <span className="h-6 w-px bg-pp-border" aria-hidden />
      <span className="flex items-baseline gap-1 text-xs text-pp-muted">
        <PayPalWordmark className="text-base" /> sandbox
      </span>
      <span className="h-6 w-px bg-pp-border" aria-hidden />
      <span className="flex items-center gap-1 text-xs text-pp-muted">
        built with <AgStudioLogo className="-ml-1 h-8 w-auto" />
      </span>
    </div>
  );
}

function TopBar() {
  return (
    <header className="flex h-16 shrink-0 items-center gap-6 border-b border-pp-border bg-white px-6">
      <BrandLockup />
      <div className="ml-auto flex items-center gap-1 text-pp-navy">
        <span className="inline-flex size-9 items-center justify-center rounded-full" title="Help">
          <HelpIcon />
        </span>
        <span className="inline-flex size-9 items-center justify-center rounded-full" title="Notifications">
          <BellIcon />
        </span>
        <span className="ml-2 flex items-center gap-2">
          <span className="hidden rounded-full border border-pp-border px-3 py-1 text-xs font-semibold text-pp-muted sm:inline">
            read-only · cannot approve or pay
          </span>
        </span>
      </div>
    </header>
  );
}

export function AppShell({
  sidebar,
  children,
}: {
  sidebar: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex h-dvh flex-col bg-pp-canvas font-paypal text-pp-ink">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <nav className="flex w-64 shrink-0 flex-col border-r border-pp-border bg-white" aria-label="Reports">
          {sidebar}
        </nav>
        <main className="flex min-w-0 flex-1 flex-col">{children}</main>
      </div>
    </div>
  );
}
