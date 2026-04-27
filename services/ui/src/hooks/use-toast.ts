// Minimal toast hook — replaces full sonner dependency for M1.4 shell.
import * as React from "react";
import type { ToastProps } from "@/components/ui/toast";

const TOAST_LIMIT = 5;
const TOAST_REMOVE_DELAY = 4000;

type ToasterToast = ToastProps & {
  id: string;
  title?: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactElement;
};

type State = { toasts: ToasterToast[] };

let count = 0;
function genId() {
  count = (count + 1) % Number.MAX_SAFE_INTEGER;
  return count.toString();
}

const listeners: Array<(state: State) => void> = [];
let memoryState: State = { toasts: [] };

function dispatch(action: { type: "ADD" | "REMOVE"; toast?: ToasterToast; toastId?: string }) {
  if (action.type === "ADD") {
    memoryState = {
      toasts: [action.toast!, ...memoryState.toasts].slice(0, TOAST_LIMIT),
    };
  } else {
    memoryState = {
      toasts: memoryState.toasts.filter((t) => t.id !== action.toastId),
    };
  }
  listeners.forEach((l) => l(memoryState));
}

export function toast({ title, description, variant = "default" }: Omit<ToasterToast, "id">) {
  const id = genId();
  dispatch({ type: "ADD", toast: { id, title, description, variant } });
  setTimeout(() => dispatch({ type: "REMOVE", toastId: id }), TOAST_REMOVE_DELAY);
}

export function useToast() {
  const [state, setState] = React.useState<State>(memoryState);
  React.useEffect(() => {
    listeners.push(setState);
    return () => {
      const idx = listeners.indexOf(setState);
      if (idx > -1) listeners.splice(idx, 1);
    };
  }, []);
  return state;
}
