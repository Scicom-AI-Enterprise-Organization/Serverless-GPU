"use client";

import { useState, useTransition } from "react";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AdminUserRecord, PolicyRole, SectionKey } from "@/lib/types";

const SECTION_LABEL: Record<SectionKey, string> = {
  inference: "Inference",
  benchmark: "Benchmark",
  compute: "Compute",
};

const ROLE_BADGE: Record<AdminUserRecord["role"], string> = {
  // Tier role — neutral. Colour is reserved for status pills, not identity.
  admin: "bg-muted text-foreground border-border",
  developer: "bg-muted text-foreground border-border",
  user: "bg-muted text-muted-foreground border-border",
};

const NO_ROLE = "__none__";

function fmtDate(iso: string) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}

export function OrganizationTable({
  users,
  policyRoles,
  currentUserId,
}: {
  users: AdminUserRecord[];
  policyRoles: PolicyRole[];
  currentUserId: number;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-5 py-3 text-left font-medium">User</th>
            <th className="px-5 py-3 text-left font-medium">Email</th>
            <th className="px-5 py-3 text-left font-medium">Tier</th>
            <th className="px-5 py-3 text-left font-medium">Policy role</th>
            <th className="px-5 py-3 text-left font-medium">Sections</th>
            <th className="px-5 py-3 text-left font-medium">Created</th>
            <th className="px-5 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {users.map((u) => (
            <UserRow
              key={u.id}
              user={u}
              policyRoles={policyRoles}
              isSelf={u.id === currentUserId}
            />
          ))}
          {users.length === 0 && (
            <tr>
              <td colSpan={7} className="px-5 py-10 text-center text-muted-foreground">
                No users.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function UserRow({
  user,
  policyRoles,
  isSelf,
}: {
  user: AdminUserRecord;
  policyRoles: PolicyRole[];
  isSelf: boolean;
}) {
  const [pending, startTransition] = useTransition();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const setTierRole = (next: AdminUserRecord["role"]) => {
    if (next === user.role) return;
    startTransition(async () => {
      try {
        const r = await fetch(`/api/proxy/admin/users/${user.id}/role`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: next }),
        });
        if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
        toast.success(`${user.username} → ${next}`);
        window.location.reload();
      } catch (e) {
        toast.error(`Failed to update tier: ${e instanceof Error ? e.message : String(e)}`);
      }
    });
  };

  const setPolicyRole = (next: string) => {
    const value = next === NO_ROLE ? null : next;
    if (value === user.policy_role_id) return;
    startTransition(async () => {
      try {
        const r = await fetch(`/api/proxy/admin/users/${user.id}/policy-role`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ policy_role_id: value }),
        });
        if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
        toast.success(value ? `${user.username} → ${value}` : `${user.username} detached from role`);
        window.location.reload();
      } catch (e) {
        toast.error(`Failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    });
  };

  const deleteUser = () => {
    setDeleteOpen(false);
    startTransition(async () => {
      try {
        const r = await fetch(`/api/proxy/admin/users/${user.id}`, { method: "DELETE" });
        if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
        toast.success(`Deleted ${user.username}`);
        window.location.reload();
      } catch (e) {
        toast.error(`Failed to delete: ${e instanceof Error ? e.message : String(e)}`);
      }
    });
  };

  // For non-developer/non-admin users (or admins), the policy-role dropdown
  // is informational only — admins always have full access regardless.
  const policyDisabled = pending || user.role === "user";

  return (
    <tr className="hover:bg-muted/30">
      <td className="px-5 py-3">
        <div className="font-medium text-foreground">{user.username}</div>
        <div className="text-xs text-muted-foreground">id={user.id}</div>
      </td>
      <td className="px-5 py-3 font-mono text-xs text-muted-foreground">{user.email ?? "—"}</td>
      <td className="px-5 py-3">
        <Select
          value={user.role}
          disabled={pending || isSelf}
          onValueChange={(v) => setTierRole(v as AdminUserRecord["role"])}
        >
          <SelectTrigger
            className="w-32 capitalize"
            title={isSelf ? "You can't change your own tier" : ""}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="user" className="capitalize">user</SelectItem>
            <SelectItem value="developer" className="capitalize">developer</SelectItem>
            <SelectItem value="admin" className="capitalize">admin</SelectItem>
          </SelectContent>
        </Select>
      </td>
      <td className="px-5 py-3">
        <Select
          value={user.policy_role_id ?? NO_ROLE}
          disabled={policyDisabled}
          onValueChange={setPolicyRole}
        >
          <SelectTrigger className="w-44" title={user.role === "user" ? "Promote to developer first" : ""}>
            <SelectValue placeholder="No role" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_ROLE}>No role (no access)</SelectItem>
            {policyRoles.map((r) => (
              <SelectItem key={r.id} value={r.id}>
                {r.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </td>
      <td className="px-5 py-3">
        <div className="flex flex-wrap gap-1">
          {(["inference", "benchmark", "compute"] as SectionKey[]).map((s) => {
            const allowed = user.section_permissions[s];
            return (
              <span
                key={s}
                className={
                  "inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-medium " +
                  (allowed
                    ? "border-border bg-muted text-foreground"
                    : "border-border bg-background text-muted-foreground/60 line-through")
                }
                title={allowed ? `Has ${s} access` : `No ${s} access`}
              >
                {SECTION_LABEL[s]}
              </span>
            );
          })}
        </div>
      </td>
      <td className="px-5 py-3 text-muted-foreground">{fmtDate(user.created_at)}</td>
      <td className="px-5 py-3 text-right">
        <Button
          variant="ghost"
          size="icon"
          disabled={pending || isSelf}
          title={isSelf ? "You can't delete yourself" : "Delete user"}
          onClick={() => setDeleteOpen(true)}
          className="text-muted-foreground hover:text-destructive"
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </td>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete {user.username}?</DialogTitle>
            <DialogDescription>
              This permanently removes the user and any endpoints they own. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={deleteUser}>
              <Trash2 className="h-4 w-4" />
              Delete user
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </tr>
  );
}
