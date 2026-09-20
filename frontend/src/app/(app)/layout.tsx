"use client";

/**
 * The signed-in shell: a slim sidebar, the workspace it belongs to, and the
 * theme switch. Anyone without a valid session is sent to /login.
 */

import { LayoutDashboard, LogOut, MessagesSquare, FileStack } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { Logo } from "@/components/brand";
import { ThemeToggle } from "@/components/theme";
import { Skeleton } from "@/components/ui";
import { useAuth } from "@/lib/auth";

const NAV = [
  { href: "/chat", label: "Ask", Icon: MessagesSquare },
  { href: "/documents", label: "Documents", Icon: FileStack },
  { href: "/admin", label: "Admin", Icon: LayoutDashboard, adminOnly: true },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, tenant, loading, isAdmin, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen">
        <div className="w-60 border-r border-line bg-surface p-4">
          <Skeleton className="h-8 w-32" />
          <div className="mt-6 space-y-2">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        </div>
        <div className="flex-1 p-8">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="mt-4 h-64 w-full" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-surface">
        <Link href="/" className="border-b border-line px-4 py-4 transition hover:bg-canvas">
          <Logo size={28} subtitle={tenant?.name} />
        </Link>

        <nav className="flex-1 space-y-1 p-3">
          {NAV.filter((item) => !item.adminOnly || isAdmin).map(({ href, label, Icon }) => {
            const active = pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`group flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition ${
                  active
                    ? "bg-brand-soft font-medium text-brand"
                    : "text-muted hover:bg-canvas hover:text-ink"
                }`}
              >
                <Icon size={17} className="transition group-hover:scale-110" />
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="space-y-3 border-t border-line p-3">
          <ThemeToggle compact />
          <div className="px-1 text-xs text-muted">
            <p className="truncate" title={user.email}>
              {user.email}
            </p>
            <p className="capitalize">{user.role}</p>
          </div>
          <button
            onClick={logout}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-xs text-muted transition hover:bg-canvas hover:text-ink"
          >
            <LogOut size={14} />
            Sign out
          </button>
        </div>
      </aside>

      <main className="min-w-0 flex-1 animate-fade-in">{children}</main>
    </div>
  );
}
