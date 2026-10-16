"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import { auth, type AuthSession, type AuthUser } from "@/lib/auth";

interface AuthContextType {
  user: AuthUser | null;
  session: AuthSession | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  session: null,
  loading: true,
  signIn: async () => {},
  signUp: async () => {},
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Initial restoration
    const initialSession = auth.restoreSession();
    setSession(initialSession);
    setLoading(false);

    // Subscribe to state changes
    const unsubscribe = auth.subscribe((updatedSession) => {
      setSession(updatedSession);
      setLoading(false);
    });

    return unsubscribe;
  }, []);

  const handleSignIn = async (email: string, password: string) => {
    const s = await auth.signIn(email, password);
    setSession(s);
  };

  const handleSignUp = async (email: string, password: string) => {
    const s = await auth.signUp(email, password);
    setSession(s);
  };

  const handleSignOut = async () => {
    await auth.signOut();
    setSession(null);
  };

  return (
    <AuthContext.Provider
      value={{
        user: session?.user ?? null,
        session,
        loading,
        signIn: handleSignIn,
        signUp: handleSignUp,
        signOut: handleSignOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
