import Link from "next/link";
import { Button } from "@/components/ui/button";

// "Continue with GitHub" — links to our /api/auth/github/start route which
// generates state, sets the cookie, and redirects to GitHub's authorize URL.
export function GithubSsoButton({ next }: { next: string }) {
  const href = `/api/auth/github/start?next=${encodeURIComponent(next)}`;
  return (
    <Button asChild variant="outline" className="w-full">
      <Link href={href}>
        <GithubMark />
        Continue with GitHub
      </Link>
    </Button>
  );
}

function GithubMark() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      aria-hidden="true"
      className="shrink-0"
      fill="currentColor"
    >
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56 0-.27-.01-1.16-.02-2.1-3.2.69-3.87-1.36-3.87-1.36-.52-1.34-1.27-1.7-1.27-1.7-1.04-.71.08-.69.08-.69 1.15.08 1.76 1.18 1.76 1.18 1.02 1.75 2.68 1.24 3.34.95.1-.74.4-1.24.73-1.52-2.55-.29-5.24-1.28-5.24-5.7 0-1.26.45-2.29 1.18-3.1-.12-.29-.51-1.46.11-3.04 0 0 .97-.31 3.18 1.18.92-.26 1.91-.39 2.89-.39.98 0 1.97.13 2.89.39 2.21-1.49 3.18-1.18 3.18-1.18.62 1.58.23 2.75.11 3.04.74.81 1.18 1.84 1.18 3.1 0 4.43-2.7 5.41-5.27 5.69.41.36.78 1.06.78 2.14 0 1.55-.01 2.79-.01 3.17 0 .31.21.67.8.56C20.21 21.39 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5Z" />
    </svg>
  );
}
