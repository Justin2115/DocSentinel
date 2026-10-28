import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ThemeProvider } from "@mui/material/styles";
import { createAppTheme } from "../styles/theme";
import type { ThemeMode } from "../styles/theme";
import { ThemeContext } from "./ThemeContextDefinition";
const STORAGE_KEY = "theme";

function getInitialMode(): ThemeMode {
	try {
		const saved = localStorage.getItem(STORAGE_KEY);
		if (saved === "light" || saved === "dark") return saved;
	} catch {
		// Use the system preference when browser storage is unavailable.
	}
	if (typeof document !== "undefined") {
		const bootstrapped = document.documentElement.dataset.theme;
		if (bootstrapped === "light" || bootstrapped === "dark") return bootstrapped;
	}
	return typeof matchMedia !== "undefined" && matchMedia("(prefers-color-scheme: light)").matches
		? "light"
		: "dark";
}

function applyRootMode(mode: ThemeMode) {
	if (typeof document === "undefined") return;
	const root = document.documentElement;
	root.dataset.theme = mode;
	root.classList.toggle("dark", mode === "dark");
	document.body?.classList.toggle("dark-mode", mode === "dark");
}

export function AppThemeProvider({ children }: { children: ReactNode }) {
	const [mode, setModeState] = useState<ThemeMode>(getInitialMode);
	const muiTheme = useMemo(() => createAppTheme(mode), [mode]);

	const setMode = useCallback((nextMode: ThemeMode) => {
		applyRootMode(nextMode);
		try {
			localStorage.setItem(STORAGE_KEY, nextMode);
		} catch {
			// The active theme still changes for this session when storage is unavailable.
		}
		setModeState(nextMode);
	}, []);

	useEffect(() => {
		applyRootMode(mode);
		const preference = matchMedia("(prefers-color-scheme: light)");
		const followSystem = (event: MediaQueryListEvent) => {
			try {
				if (localStorage.getItem(STORAGE_KEY) !== null) return;
			} catch {
				// If storage is blocked, continue following the system preference.
			}
			const nextMode: ThemeMode = event.matches ? "light" : "dark";
			applyRootMode(nextMode);
			setModeState(nextMode);
		};
		preference.addEventListener("change", followSystem);
		return () => preference.removeEventListener("change", followSystem);
	}, [mode]);

	const value = useMemo(() => ({ mode, setMode }), [mode, setMode]);

	return (
		<ThemeContext.Provider value={value}>
			<ThemeProvider theme={muiTheme}>{children}</ThemeProvider>
		</ThemeContext.Provider>
	);
}
