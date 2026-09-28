"use client";

/**
 * Tells the API that somebody opened the demo, once per browser session.
 *
 * Why once per session: the point is "a person arrived", not "a page loaded".
 * Clicking around, refreshing, or signing in would otherwise each count again.
 * The session id lives in sessionStorage, so it disappears when the tab closes
 * and a return visit tomorrow counts as a new arrival — which is what you want.
 *
 * It sends the referrer (which is how you learn whether the LinkedIn post is
 * working) and nothing else. The server adds the browser and a rough location
 * and forgets the IP; see backend/app/services/notify.py.
 *
 * Every failure is swallowed: analytics must never be the reason a page breaks.
 */

import { useEffect } from "react";

import { API_URL } from "@/lib/api";

const KEY = "nexa.visit";

export function VisitPing() {
  useEffect(() => {
    let session: string | null = null;
    try {
      if (sessionStorage.getItem(KEY)) return; // already counted this session
      session = crypto.randomUUID();
      sessionStorage.setItem(KEY, session);
    } catch {
      return; // private mode or blocked storage: skip rather than double-count
    }

    // keepalive lets the request finish even if the visitor navigates away
    // immediately, which is exactly the visitor we most want to know about.
    void fetch(`${API_URL}/events/visit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      keepalive: true,
      body: JSON.stringify({
        path: window.location.pathname,
        referrer: document.referrer || null,
        session_id: session,
      }),
    }).catch(() => {});
  }, []);

  return null;
}
