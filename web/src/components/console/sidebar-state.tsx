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
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(STORAGE_KEY) === "1");
    } catch {
      // ignore
    }
  }, []);

  const togglePanel = useCallback(() => {
    const isDesktop = typeof window !== "undefined"
      && window.matchMedia("(min-width: 768px)").matches;
    if (isDesktop) {
      setCollapsed((prev) => {
        const next = !prev;
        try {
          window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
        } catch {
          // best-effort persist
        }
        return next;
      });
    } else {
      setMobileOpen((prev) => !prev);
    }
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
