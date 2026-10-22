/**
 * Supabase Auth client for BuildwiseAI frontend.
 *
 * Implements sign-up, sign-in, sign-out, session restoration, and token management
 * via standard Supabase Auth REST endpoints (no heavyweight external client required).
 * Provides automatic fallback to demo session in local development when Supabase
 * credentials are not configured.
 */

export interface AuthUser {
  id: string;
  email: string;
  role?: string;
  user_metadata?: Record<string, unknown>;
  created_at?: string;
}

export interface AuthSession {
  access_token: string;
  refresh_token: string;
  expires_at: number; // Unix epoch seconds
  user: AuthUser;
}

const STORAGE_KEY = "buildwise_auth_session";

export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
export const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";

function isSupabaseConfigured(): boolean {
  return Boolean(
    SUPABASE_URL &&
      SUPABASE_ANON_KEY &&
      !SUPABASE_URL.includes("placeholder") &&
      !SUPABASE_URL.includes("xxx")
  );
}

function setCookie(name: string, value: string, maxAgeSeconds: number) {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=${maxAgeSeconds}; SameSite=Lax`;
}

function deleteCookie(name: string) {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=; path=/; max-age=0; SameSite=Lax`;
}

/**
 * Creates a deterministic demo JWT token for local development when Supabase is not configured.
 */
function createDemoJwt(userId: string, email: string): string {
  const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const exp = Math.floor(Date.now() / 1000) + 86400 * 30; // 30 days
  const payload = btoa(
    JSON.stringify({
      sub: userId,
      email: email,
      role: "authenticated",
      exp: exp,
      aud: "authenticated",
      iss: "buildwise-demo",
    })
  );
  const signature = btoa("demo_signature");
  return `${header}.${payload}.${signature}`;
}

class AuthService {
  private currentSession: AuthSession | null = null;
  private listeners: Set<(session: AuthSession | null) => void> = new Set();
  private isInitialized = false;

  constructor() {
    if (typeof window !== "undefined") {
      this.restoreSession();
    }
  }

  public subscribe(callback: (session: AuthSession | null) => void): () => void {
    this.listeners.add(callback);
    callback(this.currentSession);
    return () => {
      this.listeners.delete(callback);
    };
  }

  private notify() {
    for (const listener of this.listeners) {
      listener(this.currentSession);
    }
  }

  public restoreSession(): AuthSession | null {
    if (typeof window === "undefined") return null;

    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        this.currentSession = null;
        this.isInitialized = true;
        return null;
      }

      const session: AuthSession = JSON.parse(raw);
      const nowSeconds = Math.floor(Date.now() / 1000);

      // Check if session has expired
      if (session.expires_at && session.expires_at <= nowSeconds) {
        // Attempt refresh if refresh_token is available
        if (session.refresh_token && isSupabaseConfigured()) {
          this.refreshSession(session.refresh_token).catch(() => {
            this.clearSession();
          });
        } else {
          this.clearSession();
        }
        return null;
      }

      this.currentSession = session;
      setCookie("sb_token", session.access_token, session.expires_at - nowSeconds);
      this.isInitialized = true;
      this.notify();
      return session;
    } catch {
      this.clearSession();
      this.isInitialized = true;
      return null;
    }
  }

  private saveSession(session: AuthSession) {
    this.currentSession = session;
    if (typeof window !== "undefined") {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
      const remainingSeconds = Math.max(session.expires_at - Math.floor(Date.now() / 1000), 3600);
      setCookie("sb_token", session.access_token, remainingSeconds);
    }
    this.notify();
  }

  private clearSession() {
    this.currentSession = null;
    if (typeof window !== "undefined") {
      localStorage.removeItem(STORAGE_KEY);
      deleteCookie("sb_token");
    }
    this.notify();
  }

  public getSession(): AuthSession | null {
    if (!this.isInitialized && typeof window !== "undefined") {
      return this.restoreSession();
    }
    return this.currentSession;
  }

  public getUser(): AuthUser | null {
    const session = this.getSession();
    return session ? session.user : null;
  }

  public getToken(): string | null {
    const session = this.getSession();
    return session ? session.access_token : null;
  }

  public async signIn(email: string, password: string): Promise<AuthSession> {
    const trimmedEmail = email.trim();

    const isDemoEmail =
      trimmedEmail === "architect@buildwise.ai" ||
      trimmedEmail.endsWith("@buildwise.ai") ||
      trimmedEmail === "demo@buildwise.ai";

    if (!isSupabaseConfigured() || isDemoEmail) {
      // Local dev / demo mode: generate a demo session
      const userId = "00000000-0000-0000-0000-000000000001";
      const token = createDemoJwt(userId, trimmedEmail);
      const session: AuthSession = {
        access_token: token,
        refresh_token: "demo-refresh-token",
        expires_at: Math.floor(Date.now() / 1000) + 86400 * 30,
        user: {
          id: userId,
          email: trimmedEmail,
          role: "authenticated",
          created_at: new Date().toISOString(),
        },
      };
      this.saveSession(session);
      return session;
    }

    // Call Supabase Auth endpoint
    const url = `${SUPABASE_URL.replace(/\/+$/, "")}/auth/v1/token?grant_type=password`;
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: SUPABASE_ANON_KEY,
      },
      body: JSON.stringify({ email: trimmedEmail, password }),
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error_description || data.msg || data.message || "Failed to sign in");
    }

    const expiresAt = Math.floor(Date.now() / 1000) + (data.expires_in || 3600);
    const session: AuthSession = {
      access_token: data.access_token,
      refresh_token: data.refresh_token,
      expires_at: expiresAt,
      user: {
        id: data.user.id,
        email: data.user.email,
        role: data.user.role || "authenticated",
        user_metadata: data.user.user_metadata,
        created_at: data.user.created_at,
      },
    };

    this.saveSession(session);
    return session;
  }

  public async signUp(email: string, password: string): Promise<AuthSession> {
    const trimmedEmail = email.trim();

    const isDemoEmail =
      trimmedEmail === "architect@buildwise.ai" ||
      trimmedEmail.endsWith("@buildwise.ai") ||
      trimmedEmail === "demo@buildwise.ai";

    if (!isSupabaseConfigured() || isDemoEmail) {
      // Local dev / demo mode: generate a demo session
      const userId = "00000000-0000-0000-0000-000000000001";
      const token = createDemoJwt(userId, trimmedEmail);
      const session: AuthSession = {
        access_token: token,
        refresh_token: "demo-refresh-token",
        expires_at: Math.floor(Date.now() / 1000) + 86400 * 30,
        user: {
          id: userId,
          email: trimmedEmail,
          role: "authenticated",
          created_at: new Date().toISOString(),
        },
      };
      this.saveSession(session);
      return session;
    }

    const url = `${SUPABASE_URL.replace(/\/+$/, "")}/auth/v1/signup`;
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: SUPABASE_ANON_KEY,
      },
      body: JSON.stringify({ email: trimmedEmail, password }),
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error_description || data.msg || data.message || "Failed to sign up");
    }

    // Some Supabase setups require email confirmation before returning a session
    if (!data.access_token && data.id) {
      throw new Error("Confirmation email sent! Please check your inbox to activate your account.");
    }

    const expiresAt = Math.floor(Date.now() / 1000) + (data.expires_in || 3600);
    const session: AuthSession = {
      access_token: data.access_token,
      refresh_token: data.refresh_token,
      expires_at: expiresAt,
      user: {
        id: data.user ? data.user.id : data.id,
        email: data.user ? data.user.email : data.email,
        role: (data.user && data.user.role) || "authenticated",
        created_at: data.created_at,
      },
    };

    this.saveSession(session);
    return session;
  }

  public async refreshSession(refreshToken: string): Promise<AuthSession | null> {
    if (!isSupabaseConfigured()) return null;

    try {
      const url = `${SUPABASE_URL.replace(/\/+$/, "")}/auth/v1/token?grant_type=refresh_token`;
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          apikey: SUPABASE_ANON_KEY,
        },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });

      if (!res.ok) {
        this.clearSession();
        return null;
      }

      const data = await res.json();
      const expiresAt = Math.floor(Date.now() / 1000) + (data.expires_in || 3600);
      const session: AuthSession = {
        access_token: data.access_token,
        refresh_token: data.refresh_token,
        expires_at: expiresAt,
        user: {
          id: data.user.id,
          email: data.user.email,
          role: data.user.role || "authenticated",
          created_at: data.user.created_at,
        },
      };

      this.saveSession(session);
      return session;
    } catch {
      this.clearSession();
      return null;
    }
  }

  public async signOut(): Promise<void> {
    const token = this.getToken();
    if (token && isSupabaseConfigured()) {
      try {
        const url = `${SUPABASE_URL.replace(/\/+$/, "")}/auth/v1/logout`;
        await fetch(url, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            apikey: SUPABASE_ANON_KEY,
          },
        });
      } catch {
        // Ignore network errors on logout
      }
    }
    this.clearSession();
  }
}

export const auth = new AuthService();
