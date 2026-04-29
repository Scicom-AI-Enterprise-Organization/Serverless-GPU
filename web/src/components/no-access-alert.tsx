import { ShieldAlert } from "lucide-react";

/**
 * AWS-IAM-style inline red alert shown when the current user lacks the role
 * needed to use the page. Fixed copy with optional message override; no
 * dismiss, no popup — just a banner at the top of the content area.
 */
export function NoAccessAlert({
  title = "You don't have permission to view this",
  message,
}: {
  title?: string;
  message?: string;
}) {
  return (
    <div
      role="alert"
      className="mb-6 flex items-start gap-3 rounded-md border border-l-4 border-destructive/40 border-l-destructive bg-destructive/5 px-4 py-3 text-sm"
    >
      <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
      <div className="flex-1 space-y-1">
        <div className="font-semibold text-destructive">{title}</div>
        <p className="text-foreground/80">
          {message ??
            "Your account doesn't have the required role for this page. Ask an admin to grant you developer access."}
        </p>
      </div>
    </div>
  );
}
