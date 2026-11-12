// The AG Studio AI assistant. The adapter calls `${endpoint}/responses`, i.e. this app's proxy at
// app/api/ai/responses, which holds the OpenAI key, so nothing secret is sent from the browser.

import { createAiHarness, type AgAiHarnessSetup, type AgAiPromptStarter } from 'ag-studio';
import { openaiAdapter } from './openai-adapter';

// Suggestions shown in a new AI conversation, before anything has been typed.
const PROMPT_STARTERS: AgAiPromptStarter[] = [
  {
    label: 'Summarise the attack results',
    prompt: 'Summarise this page: unauthorized payments in baseline vs protected mode, and how protected runs were stopped. Use raw counts.',
  },
  {
    label: 'Model vs code',
    prompt: 'In protected mode, how often did the model itself propose the malicious purchase, and what stopped it each time?',
  },
  {
    label: 'Weakest scenario',
    prompt: 'Which attack scenario came closest to succeeding in protected mode, and which rule or step stopped it?',
  },
];

// `models` are the ids allowed by OPENAI_MODELS on the server; the first is the default, and the
// picker only shows for two or more.
export function buildAi(models: string[]): AgAiHarnessSetup {
  const adapter = openaiAdapter({ endpoint: '/api/ai', model: models[0] });

  return ({ api }) =>
    createAiHarness(api, {
      adapter,
      promptStarters: PROMPT_STARTERS,
      models: models.length > 1 ? models.map((id) => ({ id, label: id })) : undefined,
    });
}
