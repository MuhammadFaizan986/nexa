"use client";

/**
 * The signed-in shell: sidebar, current organisation, sign out.
 * Anyone without a valid session is sent to /login.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/lib/auth";

const NAV = [
  { href: "/chat", label: "Ask" },
  { href: "/documents", label: "Documents" },
  { href: "/admin", label: "Admin", adminOnly: true },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, tenant, loading, isAdmin, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading || !user) {
    return <p className="p-8 text-sm text-muted">Loading…</p>;
  }

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-surface">
        <div className="border-b border-line px-4 py-4">
          <p className="text-lg font-semibold">NEXA</p>
          <p className="truncate text-xs text-muted" title={tenant?.name}>
            {tenant?.name}
          </p>
        </div>
        <nav className="flex-1 space-y-1 p-2">
          {NAV.filter((item) => !item.adminOnly || isAdmin).map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`block rounded-lg px-3 py-2 text-sm ${
                pathname.startsWith(item.href)
                  ? "bg-brand-soft font-medium text-brand"
                  : "hover:bg-canvas"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="border-t border-line px-4 py-3 text-xs text-muted">
          <p className="truncate" title={user.email}>
            {user.email}
          </p>
          <p className="capitalize">{user.role}</p>
          <button onClick={logout} className="mt-2 text-brand hover:underline">
            Sign out
          </button>
        </div>
      </aside>
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}
