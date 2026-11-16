import { useEffect, useState } from "react";

/** A remembered on/off UI preference (sidebar collapsed, list hidden). Falls back to the default if storage is blocked. */
export function usePref(key: string, initial: boolean): [boolean, (v: boolean) => void] {
  const [value, setValue] = useState(initial);
  useEffect(() => {
    try {
      const v = localStorage.getItem(key);
      if (v !== null) setValue(v === "1");
    } catch {}
  }, [key]);
  const set = (v: boolean) => {
    setValue(v);
    try {
      localStorage.setItem(key, v ? "1" : "0");
    } catch {}
  };
  return [value, set];
}
