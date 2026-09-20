"use client";

/**
 * Admin: what it costs, who can use it, and who may see what.
 *
 * The permissions section is the one to demo to a fintech or legal client:
 * create a group, put people in it, grant it a collection, and everyone else
 * gets "I don't have enough information" for those documents.
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError, request } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type {
  AccessGrant,
  Collection,
  Group,
  GroupDetail,
  TenantSettings,
  UsageReport,
  User,
} from "@/lib/types";
import { StatTile, UsageChart } from "@/components/UsageChart";
import { Button, Card, Empty, ErrorNote, Input, Select } from "@/components/ui";

const MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"];

export default function AdminPage() {
  const { reload } = useAuth();
  const [usage, setUsage] = useState<UsageReport | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [settings, setSettings] = useState<TenantSettings | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [usageReport, userList, groupList, collectionList, tenantSettings] = await Promise.all([
        request<UsageReport>("/admin/usage?days=30"),
        request<User[]>("/users"),
        request<Group[]>("/groups"),
        request<Collection[]>("/collections"),
        request<TenantSettings>("/tenants/me"),
      ]);
      setUsage(usageReport);
      setUsers(userList);
      setGroups(groupList);
      setCollections(collectionList);
      setSettings(tenantSettings);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the admin data");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const totals = usage?.totals;

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <header>
        <h1 className="text-xl font-semibold">Admin</h1>
        <p className="text-sm text-muted">Usage and cost, people, and who may read what.</p>
      </header>
      <ErrorNote>{error}</ErrorNote>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label="Questions (30 days)" value={totals?.questions ?? "—"} />
        <StatTile
          label="AI cost (30 days)"
          value={totals ? `$${totals.cost_usd.toFixed(2)}` : "—"}
          hint={totals?.questions ? `${(totals.cost_usd / totals.questions).toFixed(3)} per question` : undefined}
        />
        <StatTile
          label="Documents"
          value={totals?.documents ?? "—"}
          hint={totals ? `${totals.chunks.toLocaleString()} passages indexed` : undefined}
        />
        <StatTile label="People" value={totals?.users ?? "—"} />
      </div>

      <Card title="Cost per day">{usage && <UsageChart days={usage.days} />}</Card>

      <UsersPanel users={users} onChange={load} setError={setError} />
      <GroupsPanel groups={groups} users={users} onChange={load} setError={setError} />
      <CollectionsPanel
        collections={collections}
        groups={groups}
        onChange={load}
        setError={setError}
      />
      {settings && (
        <SettingsPanel
          settings={settings}
          onSaved={async () => {
            await load();
            await reload();
          }}
          setError={setError}
        />
      )}
    </div>
  );
}

type PanelProps = { onChange: () => Promise<void>; setError: (message: string | null) => void };

function useAction(setError: (message: string | null) => void) {
  return async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work");
    }
  };
}

function UsersPanel({ users, onChange, setError }: PanelProps & { users: User[] }) {
  const run = useAction(setError);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("member");

  return (
    <Card title="People">
      <form
        className="mb-4 flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            await request("/users", { method: "POST", body: { email, password, role } });
            setEmail("");
            setPassword("");
            await onChange();
          });
        }}
      >
        <Input
          type="email"
          placeholder="name@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <Input
          type="password"
          placeholder="Initial password (10+ characters)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={10}
          required
        />
        <Select value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="member">Member — can upload and ask</option>
          <option value="viewer">Viewer — can only ask</option>
          <option value="admin">Admin — can manage everything</option>
        </Select>
        <Button type="submit">Add person</Button>
      </form>

      <table className="w-full text-sm">
        <tbody>
          {users.map((user) => (
            <tr key={user.id} className="border-t border-line">
              <td className="py-2">{user.email}</td>
              <td className="py-2 capitalize text-muted">{user.role}</td>
              <td className="py-2 text-right">
                {user.is_active ? (
                  <Button
                    variant="ghost"
                    onClick={() =>
                      void run(async () => {
                        await request(`/users/${user.id}`, {
                          method: "PATCH",
                          body: { is_active: false },
                        });
                        await onChange();
                      })
                    }
                  >
                    Deactivate
                  </Button>
                ) : (
                  <span className="text-xs text-muted">Deactivated</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function GroupsPanel({
  groups,
  users,
  onChange,
  setError,
}: PanelProps & { groups: Group[]; users: User[] }) {
  const run = useAction(setError);
  const [name, setName] = useState("");
  const [detail, setDetail] = useState<GroupDetail | null>(null);

  return (
    <Card title="Groups">
      <form
        className="mb-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            await request("/groups", { method: "POST", body: { name } });
            setName("");
            await onChange();
          });
        }}
      >
        <Input
          placeholder="Compliance Team"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <Button type="submit">Create group</Button>
      </form>

      {groups.length === 0 ? (
        <Empty>No groups yet. Groups are how you grant access to restricted collections.</Empty>
      ) : (
        <ul className="space-y-2 text-sm">
          {groups.map((group) => (
            <li key={group.id} className="rounded-lg border border-line p-3">
              <div className="flex items-center justify-between">
                <span className="font-medium">{group.name}</span>
                <Button
                  variant="ghost"
                  onClick={() =>
                    void run(async () =>
                      setDetail(
                        detail?.id === group.id
                          ? null
                          : await request<GroupDetail>(`/groups/${group.id}`),
                      ),
                    )
                  }
                >
                  {detail?.id === group.id ? "Hide" : "Members"}
                </Button>
              </div>

              {detail?.id === group.id && (
                <div className="mt-3 space-y-2">
                  {detail.member_ids.length === 0 && (
                    <p className="text-xs text-muted">Nobody in this group yet.</p>
                  )}
                  {detail.member_ids.map((userId) => (
                    <div key={userId} className="flex items-center justify-between text-xs">
                      <span>{users.find((u) => u.id === userId)?.email ?? userId}</span>
                      <button
                        className="text-bad hover:underline"
                        onClick={() =>
                          void run(async () => {
                            await request(`/groups/${group.id}/members/${userId}`, {
                              method: "DELETE",
                            });
                            setDetail(await request<GroupDetail>(`/groups/${group.id}`));
                          })
                        }
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                  <Select
                    className="w-full"
                    value=""
                    onChange={(event) =>
                      void run(async () => {
                        await request(`/groups/${group.id}/members`, {
                          method: "POST",
                          body: { user_id: event.target.value },
                        });
                        setDetail(await request<GroupDetail>(`/groups/${group.id}`));
                      })
                    }
                  >
                    <option value="">Add someone…</option>
                    {users
                      .filter((user) => !detail.member_ids.includes(user.id))
                      .map((user) => (
                        <option key={user.id} value={user.id}>
                          {user.email}
                        </option>
                      ))}
                  </Select>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function CollectionsPanel({
  collections,
  groups,
  onChange,
  setError,
}: PanelProps & { collections: Collection[]; groups: Group[] }) {
  const run = useAction(setError);
  const [name, setName] = useState("");
  const [visibility, setVisibility] = useState("restricted");
  const [openId, setOpenId] = useState<string | null>(null);
  const [grants, setGrants] = useState<AccessGrant[]>([]);

  const openAccess = (collectionId: string) =>
    void run(async () => {
      if (openId === collectionId) return setOpenId(null);
      setGrants(await request<AccessGrant[]>(`/collections/${collectionId}/access`));
      setOpenId(collectionId);
    });

  const saveGrants = (collectionId: string, next: AccessGrant[]) =>
    void run(async () => {
      await request(`/collections/${collectionId}/access`, {
        method: "PUT",
        body: { grants: next },
      });
      setGrants(next);
    });

  return (
    <Card title="Collections">
      <form
        className="mb-4 flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            await request("/collections", { method: "POST", body: { name, visibility } });
            setName("");
            await onChange();
          });
        }}
      >
        <Input
          placeholder="Compliance"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <Select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
          <option value="restricted">Restricted — only granted groups</option>
          <option value="tenant_wide">Everyone in the organisation</option>
        </Select>
        <Button type="submit">Create collection</Button>
      </form>

      <ul className="space-y-2 text-sm">
        {collections.map((collection) => (
          <li key={collection.id} className="rounded-lg border border-line p-3">
            <div className="flex items-center justify-between">
              <div>
                <span className="font-medium">{collection.name}</span>
                <span className="ml-2 text-xs text-muted">
                  {collection.visibility === "tenant_wide" ? "everyone" : "restricted"}
                </span>
              </div>
              {collection.visibility === "restricted" && (
                <Button variant="ghost" onClick={() => openAccess(collection.id)}>
                  {openId === collection.id ? "Hide" : "Who can read it"}
                </Button>
              )}
            </div>

            {openId === collection.id && (
              <div className="mt-3 space-y-2 text-xs">
                {groups.length === 0 && <p className="text-muted">Create a group first.</p>}
                {groups.map((group) => {
                  const grant = grants.find((g) => g.group_id === group.id);
                  return (
                    <div key={group.id} className="flex items-center justify-between">
                      <span>{group.name}</span>
                      <Select
                        value={grant?.permission ?? ""}
                        onChange={(event) => {
                          const others = grants.filter((g) => g.group_id !== group.id);
                          const value = event.target.value as "" | "read" | "write";
                          saveGrants(
                            collection.id,
                            value ? [...others, { group_id: group.id, permission: value }] : others,
                          );
                        }}
                      >
                        <option value="">No access</option>
                        <option value="read">Read</option>
                        <option value="write">Read and upload</option>
                      </Select>
                    </div>
                  );
                })}
              </div>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function SettingsPanel({
  settings,
  onSaved,
  setError,
}: {
  settings: TenantSettings;
  onSaved: () => Promise<void>;
  setError: (message: string | null) => void;
}) {
  const run = useAction(setError);
  const [form, setForm] = useState({
    name: settings.name,
    assistant_name: settings.assistant_name ?? "",
    tone: settings.tone ?? "",
    model: settings.model ?? "",
  });
  const [saved, setSaved] = useState(false);

  return (
    <Card title="Assistant settings">
      <form
        className="grid gap-3 md:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            await request("/tenants/me", {
              method: "PATCH",
              body: {
                name: form.name,
                assistant_name: form.assistant_name || null,
                tone: form.tone || null,
                model: form.model || null,
              },
            });
            setSaved(true);
            await onSaved();
          });
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block font-medium">Organisation name</span>
          <Input
            className="w-full"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Assistant name</span>
          <Input
            className="w-full"
            placeholder="Kes"
            value={form.assistant_name}
            onChange={(e) => setForm({ ...form, assistant_name: e.target.value })}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Tone</span>
          <Input
            className="w-full"
            placeholder="formal and concise"
            value={form.tone}
            onChange={(e) => setForm({ ...form, tone: e.target.value })}
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Model</span>
          <Select
            className="w-full"
            value={form.model}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
          >
            <option value="">Default ({MODELS[0]})</option>
            {MODELS.map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </Select>
        </label>
        <div className="md:col-span-2 flex items-center gap-3">
          <Button type="submit">Save settings</Button>
          {saved && <span className="text-xs text-good">Saved</span>}
        </div>
      </form>
    </Card>
  );
}
