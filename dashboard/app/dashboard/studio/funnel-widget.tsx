'use client';

// Custom AG Studio widget: the "defense funnel". Each stage is a measure (a summed 0/1 flag), drawn
// as a bar sized against the first stage, with the drop between stages labelled. Built for the
// question this dashboard exists to answer: of the attack runs, how many reached each step, and
// where did they stop? It queries data through Studio's widget API, so page filters, widget
// filters and cross filters apply exactly as they do to the built-in widgets.

import { useEffect, useState } from 'react';
import type { AgWidgetField, AgWidgetParams } from 'ag-studio';
import type { AgWidgetDefinition } from 'ag-studio-react';

type FunnelState = {
  dataMapping: { stages: AgWidgetField[] };
  format?: { title?: { enabled?: boolean; text?: string } };
};

type Stage = { label: string; value: number; text: string };

// Rows come back keyed by field; find a field's value without depending on the exact key format.
function valueFor(row: Record<string, unknown>, field: AgWidgetField, index: number): number {
  const f = field as { id?: string; aggregation?: string };
  const keys = Object.keys(row);
  const key =
    keys.find((k) => k === f.id) ??
    keys.find((k) => f.id && k.includes(f.id) && (!f.aggregation || k.includes(f.aggregation))) ??
    keys[index];
  const v = key === undefined ? 0 : Number(row[key]);
  return Number.isFinite(v) ? v : 0;
}

function DefenseFunnel(params: AgWidgetParams<FunnelState>) {
  const { widgetApi, dataMapping } = params;
  const stages = dataMapping.stages ?? [];
  const [rows, setRows] = useState<Stage[] | null>(null);

  useEffect(() => {
    let live = true;
    if (!stages.length) {
      setRows([]);
      return;
    }
    widgetApi.setDisplayState('loading', { prominent: false });
    widgetApi
      .getData({ fields: stages })
      .then(({ results }) => {
        if (!live) return;
        const row = results.rows[0] ?? {};
        setRows(
          stages.map((field, i) => {
            const value = valueFor(row, field, i);
            const label = widgetApi.getFieldName(field).replace(/\s*\((sum|avg|count|countd)\)$/i, '');
            return { label, value, text: widgetApi.formatFieldValue(field, value) };
          }),
        );
        widgetApi.setDisplayState('displayed');
      })
      .catch(() => live && widgetApi.setDisplayState('noData'));
    return () => {
      live = false;
    };
    // `params` is a new object whenever Studio re-renders the widget (data, filters, mapping).
  }, [params]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!rows) return null;
  if (!rows.length)
    return <div className="flex h-full items-center justify-center text-sm text-pp-muted">Add measures to Stages</div>;

  const top = Math.max(rows[0]?.value ?? 0, 1);
  return (
    <div className="flex h-full flex-col justify-center gap-1.5 px-2 py-1 font-paypal" role="list">
      {rows.map((s, i) => {
        const last = i === rows.length - 1;
        const pct = Math.max((s.value / top) * 100, s.value > 0 ? 2 : 0);
        const prev = i > 0 ? rows[i - 1].value : null;
        const color = last ? (s.value > 0 ? 'var(--pp-danger)' : 'var(--pp-success)') : i === 0 ? 'var(--pp-navy)' : 'var(--pp-blue)';
        return (
          <div key={i} role="listitem" className="grid grid-cols-[minmax(120px,38%)_1fr] items-center gap-3">
            <div className="truncate text-right text-[13px] text-pp-muted" title={s.label}>
              {s.label}
            </div>
            <div className="flex items-center gap-2">
              <div className="h-6 shrink-0 rounded-[4px] transition-[width] duration-500" style={{ width: `calc((100% - 120px) * ${pct / 100})`, background: color }} />
              <span className={`whitespace-nowrap text-sm tabular-nums ${last ? 'font-bold' : 'font-semibold'}`} style={{ color: last ? color : undefined }}>
                {s.text}
                <span className="ml-1 font-normal text-pp-muted">/ {rows[0].text}</span>
              </span>
              {prev !== null && prev > s.value && (
                <span className="whitespace-nowrap text-xs text-pp-muted">−{prev - s.value}</span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const FUNNEL_ICON =
  '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M2 3h12v2H2zm2 4h8v2H4zm2 4h4v2H6z"/></svg>';

export const defenseFunnelWidget: AgWidgetDefinition<'defense-funnel', FunnelState> = {
  id: 'defense-funnel',
  label: 'Defense funnel',
  icon: FUNNEL_ICON,
  dataMapping: {
    stages: {
      type: 'fieldset',
      supportedRoles: ['numeric'],
      required: true,
      intent: 'value',
      aiDescription: 'Ordered funnel stages, widest first; each is a summed 0/1 flag such as evalRuns.maliciousProposed.',
    },
  },
  form: (p) => p.createDefaults({ dataMappingItems: [{ key: 'stages', label: 'Stages (widest first)' }] }),
  comp: DefenseFunnel,
  defaultSize: { width: 8, height: 14 },
  minSize: { width: 6, height: 8 },
  ai: {
    label: 'Defense funnel',
    description: 'Horizontal funnel of summed measures, each bar sized against the first, last bar red if above zero.',
    usage: 'Use to show how many attack runs reached each step: page read, malicious purchase proposed, payment executed.',
  },
};
