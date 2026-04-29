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

export type OrgUser = {
  id: number;
  username: string;
  email: string | null;
  role: "user" | "developer" | "admin";
  is_admin: boolean;
  created_at: string;
};

const ROLE_BADGE: Record<OrgUser["role"], string> = {
  admin: "bg-violet-500/15 text-violet-700 dark:text-violet-300 border-violet-500/30",
  developer: "bg-blue-500/15 text-blue-700 dark:text-blue-300 border-blue-500/30",
  user: "bg-muted text-muted-foreground border-border",
};

function fmtDate(iso: string) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}

export function OrganizationTable({ users, currentUserId }: { users: OrgUser[]; currentUserId: number }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-5 py-3 text-left font-medium">User</th>
            <th className="px-5 py-3 text-left font-medium">Email</th>
            <th className="px-5 py-3 text-left font-medium">Role</th>
            <th className="px-5 py-3 text-left font-medium">Created</th>
            <th className="px-5 py-3 text-left font-medium">Change role</th>
            <th className="px-5 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {users.map((u) => (
            <UserRow key={u.id} user={u} isSelf={u.id === currentUserId} />
          ))}
          {users.length === 0 && (
            <tr>
              <td colSpan={6} className="px-5 py-10 text-center text-muted-foreground">
                No users.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function UserRow({ user, isSelf }: { user: OrgUser; isSelf: boolean }) {
  const [pending, startTransition] = useTransition();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const setRole = (next: OrgUser["role"]) => {
    if (next === user.role) return;
    startTransition(async () => {
      try {
        const r = await fetch(`/api/proxy/admin/users/${user.id}/role`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: next }),
        });
        if (!r.ok) {
          const txt = await r.text();
          throw new Error(`${r.status}: ${txt}`);
        }
        toast.success(`${user.username} → ${next}`);
        window.location.reload();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        toast.error(`Failed to update role: ${msg}`);
      }
    });
  };

  const deleteUser = () => {
    setDeleteOpen(false);
    startTransition(async () => {
      try {
        const r = await fetch(`/api/proxy/admin/users/${user.id}`, { method: "DELETE" });
        if (!r.ok) {
          const txt = await r.text();
          throw new Error(`${r.status}: ${txt}`);
        }
        toast.success(`Deleted ${user.username}`);
        window.location.reload();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        toast.error(`Failed to delete: ${msg}`);
      }
    });
  };

  return (
    <tr className="hover:bg-muted/30">
      <td className="px-5 py-3">
        <div className="font-medium text-foreground">{user.username}</div>
        <div className="text-xs text-muted-foreground">id={user.id}</div>
      </td>
      <td className="px-5 py-3 font-mono text-xs text-muted-foreground">{user.email ?? "—"}</td>
      <td className="px-5 py-3">
        <span
          className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium capitalize ${ROLE_BADGE[user.role]}`}
        >
          {user.role}
        </span>
      </td>
      <td className="px-5 py-3 text-muted-foreground">{fmtDate(user.created_at)}</td>
      <td className="px-5 py-3">
        <Select
          value={user.role}
          disabled={pending || isSelf}
          onValueChange={(v) => setRole(v as OrgUser["role"])}
        >
          <SelectTrigger
            className="w-36 capitalize"
            title={isSelf ? "You can't change your own role" : ""}
          >
            <SelectValue placeholder="Select role" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="user" className="capitalize">user</SelectItem>
            <SelectItem value="developer" className="capitalize">developer</SelectItem>
            <SelectItem value="admin" className="capitalize">admin</SelectItem>
          </SelectContent>
        </Select>
      </td>
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
