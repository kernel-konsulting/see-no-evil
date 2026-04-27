const AUTH_STORAGE_KEY = "sne_authenticated";

function getLocalStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    const storage = window.localStorage;
    if (
      typeof storage?.getItem !== "function" ||
      typeof storage.setItem !== "function" ||
      typeof storage.removeItem !== "function"
    ) {
      return null;
    }
    return storage;
  } catch {
    return null;
  }
}

export function getStoredAuth(): boolean {
  const storage = getLocalStorage();
  return storage?.getItem(AUTH_STORAGE_KEY) === "true";
}

export function setStoredAuth(): void {
  getLocalStorage()?.setItem(AUTH_STORAGE_KEY, "true");
}

export function clearStoredAuth(): void {
  getLocalStorage()?.removeItem(AUTH_STORAGE_KEY);
}
