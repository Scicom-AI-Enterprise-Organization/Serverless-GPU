"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

type Ctx = {
  collapsed: boolean;
  mobileOpen: boolean;
  togglePanel: () => void;
  closeMobile: () => void;
};

const SidebarStateContext = createContext<Ctx | null>(null);

const STORAGE_KEY = "serverless-ui:sidebar-collapsed";

export function SidebarStateProvider({ children }: { children: React.ReactNode }) {
  // Desktop collapse is intentionally disabled — the sidebar is always
  // expanded so admin badges + section icons stay visible. We keep the
  // context shape so the topbar's mobile menu button still works.
  const collapsed = false;
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    // Clean up any previously persisted "collapsed" preference so it
    // doesn't try to apply on next visit.
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
  }, []);

  const togglePanel = useCallback(() => {
    setMobileOpen((prev) => !prev);
  }, []);

  const closeMobile = useCallback(() => setMobileOpen(false), []);

  const value = useMemo(
    () => ({ collapsed, mobileOpen, togglePanel, closeMobile }),
    [collapsed, mobileOpen, togglePanel, closeMobile],
  );
  return <SidebarStateContext.Provider value={value}>{children}</SidebarStateContext.Provider>;
}

export function useSidebarState() {
  return (
    useContext(SidebarStateContext) ?? {
      collapsed: false,
      mobileOpen: false,
      togglePanel: () => {},
      closeMobile: () => {},
    }
  );
}
