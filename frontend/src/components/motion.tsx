import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

/** True when the OS asks for less motion. */
export function prefersReducedMotion(): boolean {
  return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
}

// ---- animated numbers ----------------------------------------------------------

const easeOut = (t: number) => 1 - (1 - t) ** 3;

/** Counts from the previous value to the new one, keeping the number's own
 * decimals. Non-numbers render as they are. */
export function AnimatedValue({ value }: { value: ReactNode }) {
  const target = typeof value === "number" && Number.isFinite(value) ? value : null;
  const [shown, setShown] = useState(target ?? 0);
  const from = useRef(target ?? 0);

  useEffect(() => {
    if (target === null) return;
    const start = from.current;
    if (start === target || prefersReducedMotion()) {
      from.current = target;
      setShown(target);
      return;
    }
    const t0 = performance.now();
    const duration = Math.min(900, 350 + Math.abs(target - start) * 25);
    let frame = 0;
    const step = (now: number) => {
      const p = Math.min(1, (now - t0) / duration);
      const v = start + (target - start) * easeOut(p);
      from.current = v;
      setShown(v);
      if (p < 1) frame = requestAnimationFrame(step);
      else from.current = target;
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target]);

  if (target === null) return <>{value}</>;
  const decimals = Number.isInteger(target) ? 0 : Math.min(2, String(target).split(".")[1]?.length ?? 0);
  return <>{shown.toFixed(decimals)}</>;
}

// ---- skeletons ----------------------------------------------------------------

export function Skeleton({ width, height = 12, className = "" }: { width?: number | string; height?: number; className?: string }) {
  return <span className={`skeleton ${className}`} style={{ width, height }} aria-hidden />;
}

/** Placeholder shaped like a table or list while data loads. */
export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="skeleton-rows" aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton-row" style={{ animationDelay: `${i * 60}ms` }}>
          <Skeleton width="14%" />
          <Skeleton width={`${38 - (i % 3) * 7}%`} />
          <Skeleton width="12%" height={18} className="pill" />
          <Skeleton width="16%" />
        </div>
      ))}
    </div>
  );
}

/** Placeholder for a whole page: header, a row of stats and a card. */
export function SkeletonPage() {
  return (
    <div className="skeleton-page" aria-hidden>
      <Skeleton width={240} height={26} />
      <Skeleton width={380} height={12} />
      <div className="stats">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="stat">
            <Skeleton width="45%" />
            <Skeleton width="35%" height={24} />
          </div>
        ))}
      </div>
      <div className="card">
        <SkeletonRows rows={5} />
      </div>
    </div>
  );
}

// ---- confirm dialog -------------------------------------------------------------

interface ConfirmOptions {
  title?: string;
  confirmLabel?: string;
  danger?: boolean;
}

type ConfirmFn = (message: string, options?: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn>((message) => Promise.resolve(window.confirm(message)));

/** Themed, animated replacement for window.confirm: `if (await confirm("…")) …` */
export function useConfirm(): ConfirmFn {
  return useContext(ConfirmContext);
}

interface Pending extends ConfirmOptions {
  message: string;
  resolve: (ok: boolean) => void;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [closing, setClosing] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const confirmBtn = useRef<HTMLButtonElement>(null);

  const confirm = useCallback<ConfirmFn>(
    (message, options = {}) =>
      new Promise<boolean>((resolve) => {
        setClosing(false);
        setPending({ message, ...options, resolve });
      }),
    [],
  );

  useEffect(() => {
    const el = dialog.current;
    if (pending && el && !el.open) {
      el.showModal();
      confirmBtn.current?.focus();
    }
  }, [pending]);

  const finish = (ok: boolean) => {
    if (!pending || closing) return;
    pending.resolve(ok);
    const done = () => {
      dialog.current?.close();
      setPending(null);
      setClosing(false);
    };
    if (prefersReducedMotion()) return done();
    setClosing(true);
    window.setTimeout(done, 170);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <dialog
        ref={dialog}
        className={`confirm${closing ? " closing" : ""}`}
        onCancel={(e) => {
          e.preventDefault();
          finish(false);
        }}
        onClick={(e) => {
          if (e.target === dialog.current) finish(false); // backdrop
        }}
        aria-labelledby="confirm-title"
      >
        {pending && (
          <div className="confirm-body">
            <h2 id="confirm-title">{pending.title ?? "Are you sure?"}</h2>
            <p>{pending.message}</p>
            <div className="form-actions">
              <button className="btn" onClick={() => finish(false)}>
                Cancel
              </button>
              <button ref={confirmBtn} className={`btn ${pending.danger === false ? "btn-primary" : "btn-danger-solid"}`} onClick={() => finish(true)}>
                {pending.confirmLabel ?? "Confirm"}
              </button>
            </div>
          </div>
        )}
      </dialog>
    </ConfirmContext.Provider>
  );
}
