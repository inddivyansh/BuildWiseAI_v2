export interface AIModel {
  id: string;
  name: string;
  provider: string;
  provider_name: string;
  category?: string;
  category_name?: string;
  tier: "free" | "premium";
  badge: string;
  capabilities: string;
  description: string;
  is_available: boolean;
  is_selectable: boolean;
  context_window: string;
  requires_key: boolean;
  env_key_name: string;
}

export interface AIProvider {
  id: string;
  name: string;
  tier: "free" | "premium";
  is_configured: boolean;
  is_selectable: boolean;
  env_key_name: string;
  help_url: string;
  description: string;
}

export interface ModelsResponse {
  models: AIModel[];
  providers: AIProvider[];
  default_model_id: string;
  default_provider: string;
  allowlist: string[];
}
