import { useContext } from "react";
import { ThemeContext } from "./ThemeContextDefinition";

export function useThemeMode() {
	const context = useContext(ThemeContext);
	if (!context) throw new Error("useThemeMode must be used within AppThemeProvider");
	return context;
}
