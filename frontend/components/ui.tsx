"use client";

import { AlertTriangle, CheckCircle2, ChevronRight, Circle, Info, Loader2, Lock, X, XCircle } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { locate } from "@/lib/nav";

/* ---------- buttons (primary / secondary / ghost / danger; md 32px, sm 24px) ---------- */

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  icon?: React.ReactNode;
  loading?: boolean;
};

const VARIANT = {
  primary: "bg-accent text-white hover:bg-accent-hover border border-transparent shadow-[inset_0_-1px_0_rgb(0_0_0/0.15)]",
  secondary: "bg-panel text-foreground border border-line hover:bg-hover shadow-raised",
  ghost: "text-text2 hover:bg-hover hover:text-foreground border border-transparent",
  danger: "bg-bad text-white hover:opacity-90 border border-transparent",
};

export function Button({ variant = "secondary", size = "md", icon, loading, className = "", children, disabled, ...p }: ButtonProps) {
  return (
    <button
      {...p}
      disabled={disabled || loading}
      className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-50
        ${size === "sm" ? "h-7 px-2 text-xs" : "h-8 px-3 text-sm"} ${VARIANT[variant]} ${className}`}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

/* ---------- page header: breadcrumb, title, one-line description, actions top-right ---------- */

export function PageHeader({ title, description, actions }: { title: string; description?: React.ReactNode; actions?: React.ReactNode }) {
  const here = locate(usePathname());
  return (
    <header className="border-b border-line bg-panel px-6 pt-4 pb-5">
      {here && (
        <nav aria-label="Breadcrumb" className="mb-1.5 flex items-center gap-1 text-xs text-muted">
          <Link href="/home" className="hover:text-foreground hover:underline">Home</Link>
          {here.section && <><ChevronRight className="h-3 w-3" aria-hidden /><span>{here.section}</span></>}
          {here.item.label !== title && <><ChevronRight className="h-3 w-3" aria-hidden />
            <Link href={here.item.href} className="hover:text-foreground hover:underline">{here.item.label}</Link></>}
        </nav>
      )}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 lg:flex-1">
          <h1 className="text-2xl leading-8 font-semibold tracking-[-0.01em]">{title}</h1>
          {description && <p className="mt-1 max-w-3xl text-sm text-text2">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

export function Page({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  return <div className={`space-y-6 px-6 py-6 ${wide ? "" : "max-w-6xl"}`}>{children}</div>;
}

export function Panel({ title, description, actions, children, flush }: {
  title?: string; description?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; flush?: boolean;
}) {
  return (
    <section className="rounded-md border border-line bg-panel shadow-raised">
      {(title || actions) && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-semibold">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}

/* ---------- status: color + icon + label, never color alone ---------- */

const STATUS = {
  success: { cls: "bg-ok-weak text-ok", Icon: CheckCircle2 },
  warning: { cls: "bg-warn-weak text-warn", Icon: AlertTriangle },
  error: { cls: "bg-bad-weak text-bad", Icon: XCircle },
  info: { cls: "bg-accent-weak text-accent", Icon: Info },
  neutral: { cls: "bg-hover text-text2", Icon: Circle },
  running: { cls: "bg-accent-weak text-accent", Icon: Loader2 },
  restricted: { cls: "bg-hover text-text2", Icon: Lock },
};

export function Status({ kind, children, title }: { kind: keyof typeof STATUS; children: React.ReactNode; title?: string }) {
  const { cls, Icon } = STATUS[kind];
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap ${cls}`}>
      <Icon className={`h-3.5 w-3.5 ${kind === "running" ? "animate-spin" : ""}`} aria-hidden />
      {children}
    </span>
  );
}

/* ---------- forms: label above, helper below, error replaces helper ---------- */

export function Field({ label, helper, error, required, children }: {
  label: string; helper?: React.ReactNode; error?: string; required?: boolean; children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-text2">
        {label}
        {required && <span className="text-bad"> *</span>}
      </span>
      {children}
      {error ? <span className="mt-1 block text-xs text-bad">{error}</span> : helper && <span className="mt-1 block text-xs text-muted">{helper}</span>}
    </label>
  );
}

export const inputCls =
  "h-8 w-full rounded border border-line bg-panel px-2 text-sm outline-none placeholder:text-muted focus:border-accent focus:ring-1 focus:ring-accent";

export function EmptyState({ icon, title, body, action }: { icon?: React.ReactNode; title: string; body?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 px-4 py-8">
      {icon && <div className="text-muted">{icon}</div>}
      <h3 className="text-sm font-semibold">{title}</h3>
      {body && <p className="max-w-lg text-sm text-text2">{body}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-hover ${className}`} aria-hidden />;
}

/* ---------- dialogs: confirm (danger variant) and prompt, via a promise ---------- */

type DialogOpts = { title: string; body?: React.ReactNode; action: string; danger?: boolean; input?: { label: string; value: string } };
type DialogState = DialogOpts & { resolve: (v: string | boolean | null) => void };

const UIContext = createContext<{
  confirm: (o: DialogOpts) => Promise<boolean>;
  prompt: (o: DialogOpts & { input: { label: string; value: string } }) => Promise<string | null>;
  toast: (text: string, kind?: "success" | "error" | "info") => void;
} | null>(null);

export const useUI = () => useContext(UIContext)!;

export function UIProvider({ children }: { children: React.ReactNode }) {
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [toasts, setToasts] = useState<{ id: number; text: string; kind: "success" | "error" | "info" }[]>([]);

  const confirm = useCallback((o: DialogOpts) => new Promise<boolean>((resolve) => setDialog({ ...o, resolve: (v) => resolve(Boolean(v)) })), []);
  const prompt = useCallback((o: DialogOpts & { input: { label: string; value: string } }) =>
    new Promise<string | null>((resolve) => setDialog({ ...o, resolve: (v) => resolve(typeof v === "string" ? v : null) })), []);
  const toast = useCallback((text: string, kind: "success" | "error" | "info" = "success") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000);
  }, []);

  return (
    <UIContext.Provider value={{ confirm, prompt, toast }}>
      {children}
      {dialog && <Dialog d={dialog} close={(v) => { dialog.resolve(v); setDialog(null); }} />}
      <div className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-80 flex-col gap-2" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className="pointer-events-auto flex items-start gap-2 rounded-md border border-line bg-panel p-3 text-sm shadow-overlay">
            <Status kind={t.kind === "error" ? "error" : t.kind === "info" ? "info" : "success"}>{t.kind === "error" ? "Error" : t.kind === "info" ? "Note" : "Done"}</Status>
            <span className="flex-1">{t.text}</span>
            <button aria-label="Dismiss" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))} className="text-muted hover:text-foreground"><X className="h-4 w-4" /></button>
          </div>
        ))}
      </div>
    </UIContext.Provider>
  );
}

function Dialog({ d, close }: { d: DialogState; close: (v: string | boolean | null) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState(d.input?.value ?? "");
  useEffect(() => {
    ref.current?.showModal(); // native modal: focus trap + Esc handled by the browser
  }, []);
  return (
    <dialog ref={ref} onClose={() => close(null)} className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-md border border-line bg-panel p-0 text-foreground shadow-overlay">
      <form method="dialog" onSubmit={(e) => { e.preventDefault(); close(d.input ? value.trim() || null : true); }}>
        <div className="flex items-start justify-between gap-4 px-5 pt-5">
          <h2 className="text-base font-semibold">{d.title}</h2>
          <button type="button" aria-label="Close" onClick={() => close(null)} className="text-muted hover:text-foreground"><X className="h-4 w-4" /></button>
        </div>
        <div className="space-y-3 px-5 py-3 text-sm text-text2">
          {d.body}
          {d.input && (
            <Field label={d.input.label}>
              <input autoFocus value={value} onChange={(e) => setValue(e.target.value)} className={inputCls} />
            </Field>
          )}
        </div>
        <div className="mt-2 grid grid-cols-2 border-t border-line">
          <button type="button" onClick={() => close(null)} className="h-12 bg-hover text-sm font-medium hover:bg-line">Cancel</button>
          <button type="submit" autoFocus={!d.input} className={`h-12 text-sm font-medium text-white ${d.danger ? "bg-bad hover:opacity-90" : "bg-accent hover:bg-accent-hover"}`}>
            {d.action}
          </button>
        </div>
      </form>
    </dialog>
  );
}

/* ---------- side panel (drawer): create forms, previews ---------- */

export function SidePanel({ title, description, onClose, children, footer, wide }: {
  title: React.ReactNode; description?: React.ReactNode; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal(); // native modal: focus trap + Esc
  }, []);
  return (
    <dialog ref={ref} onClose={onClose}
      className={`fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-dvh border-l border-line bg-panel p-0 text-foreground shadow-overlay ${wide ? "w-[min(64rem,100vw)]" : "w-[min(36rem,100vw)]"}`}>
      <div className="flex h-full flex-col">
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold">{title}</h2>
            {description && <div className="mt-0.5 text-sm text-text2">{description}</div>}
          </div>
          <button aria-label="Close" onClick={onClose} className="text-muted hover:text-foreground"><X className="h-5 w-5" /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </dialog>
  );
}
