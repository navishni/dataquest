import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { X } from "lucide-react";

interface ToastItem { id: number; kind: "info" | "error"; text: string }
interface ToastCtx { push: (kind: ToastItem["kind"], text: string) => void }
const Ctx = createContext<ToastCtx | null>(null);
let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((kind: ToastItem["kind"], text: string) => {
    const id = nextId++;
    setItems((xs) => [...xs, { id, kind, text }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 8000);
  }, []);
  return (
    <Ctx.Provider value={{ push }}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`card flex items-start gap-2 p-3 text-sm shadow-lg ${t.kind === "error" ? "border-danger/50" : ""}`}>
            <span className="flex-1 break-words">{t.text}</span>
            <button aria-label="Dismiss notification" className="text-muted hover:text-fg" onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))}>
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
export function useToast(): ToastCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useToast outside ToastProvider");
  return v;
}
