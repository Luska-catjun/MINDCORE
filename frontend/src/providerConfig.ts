export type Provider = "gemini" | "groq" | "anthropic" | "xai" | "openai";

export const PROVIDERS: readonly { id: Provider; label: string; defaultModel: string }[] = [
  { id: "gemini", label: "Gemini", defaultModel: "gemini-3.5-flash-lite" },
  { id: "groq", label: "Groq", defaultModel: "qwen/qwen3.6-27b" },
  { id: "anthropic", label: "Claude", defaultModel: "claude-sonnet-5" },
  { id: "xai", label: "Grok", defaultModel: "grok-4.6" },
  { id: "openai", label: "OpenAI (GPT)", defaultModel: "gpt-5.6-luna" },
] as const;

export const providerDefinition = (provider: Provider) => PROVIDERS.find((item) => item.id === provider) ?? PROVIDERS[0];

export const defaultProviderModels = (): Record<Provider, string> => Object.fromEntries(PROVIDERS.map((provider) => [provider.id, provider.defaultModel])) as Record<Provider, string>;
export const emptyProviderValues = (): Record<Provider, string> => Object.fromEntries(PROVIDERS.map((provider) => [provider.id, ""])) as Record<Provider, string>;
export const falseProviderValues = (): Record<Provider, boolean> => Object.fromEntries(PROVIDERS.map((provider) => [provider.id, false])) as Record<Provider, boolean>;
