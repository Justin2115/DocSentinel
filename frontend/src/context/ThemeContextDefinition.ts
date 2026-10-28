import { createContext } from "react";
import type { ThemeMode } from "../styles/theme";

export type ThemeContextValue = {
	mode: ThemeMode;
	setMode: (mode: ThemeMode) => void;
};

export const ThemeContext = createContext<ThemeContextValue | null>(null);
