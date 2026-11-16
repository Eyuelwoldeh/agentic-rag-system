import { BarChart3, BellRing, CalendarClock, History, House, LayoutDashboard, LibraryBig, MessageSquareText, Plug, ScrollText, Settings, Sigma } from "lucide-react";

export type NavItem = { href: string; label: string; Icon: typeof House; hint: string };
export type NavSection = { label: string | null; admin?: boolean; items: NavItem[] };

/** One map for the side menu, breadcrumbs and the ⌘K palette, grouped by job rather than by feature. */
export const SECTIONS: NavSection[] = [
  { label: null, items: [
    { href: "/home", label: "Home", Icon: House, hint: "Key numbers, what needs attention, recent work" },
    { href: "/", label: "Ask", Icon: MessageSquareText, hint: "Ask a question about your documents and data" },
    { href: "/dashboard", label: "Dashboards", Icon: LayoutDashboard, hint: "Charts you pinned from answers" },
  ] },
  { label: "Analytics", items: [
    { href: "/metrics", label: "Metrics", Icon: Sigma, hint: "Official definitions of key numbers" },
    { href: "/reports", label: "Reports", Icon: CalendarClock, hint: "Questions and dashboards sent on a schedule" },
    { href: "/alerts", label: "Alerts", Icon: BellRing, hint: "Get told when a number moves" },
  ] },
  { label: "Knowledge", items: [
    { href: "/library", label: "Library", Icon: LibraryBig, hint: "Uploaded documents and spreadsheets" },
    { href: "/runs", label: "Question history", Icon: History, hint: "Every question asked, with sources and checks" },
  ] },
  { label: "Administration", admin: true, items: [
    { href: "/connectors", label: "Connectors", Icon: Plug, hint: "Slack, Drive, warehouses and business systems" },
    { href: "/usage", label: "Usage", Icon: BarChart3, hint: "Questions, people and AI spend" },
    { href: "/audit", label: "Audit log", Icon: ScrollText, hint: "Who did what, and when" },
  ] },
];

export const SETTINGS: NavItem = { href: "/settings", label: "Settings", Icon: Settings, hint: "AI model, team, sign-in and your account" };

export const isActive = (href: string, path: string) => (href === "/" ? path === "/" : path.startsWith(href));

/** Section and item for the current page, for breadcrumbs. */
export function locate(path: string): { section: string | null; item: NavItem } | null {
  for (const s of SECTIONS) for (const i of s.items) if (isActive(i.href, path)) return { section: s.label, item: i };
  return isActive(SETTINGS.href, path) ? { section: null, item: SETTINGS } : null;
}
