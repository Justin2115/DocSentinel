import { createTheme } from "@mui/material/styles";

export type ThemeMode = "light" | "dark";

const readToken = (name: string, fallback: string) => {
	if (typeof document === "undefined") return fallback;
	return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
};

export function createAppTheme(mode: ThemeMode) {
	const dark = mode === "dark";
	const background = readToken("--background", dark ? "#000000" : "#FFFFFF");
	const surface = readToken("--surface", dark ? "#0A0A0A" : "#FFFFFF");
	const border = readToken("--border", dark ? "#262626" : "#E2E8F0");
	const textPrimary = readToken("--text-primary", dark ? "#FAFAFA" : "#0F172A");
	const textSecondary = readToken("--text-secondary", dark ? "#A1A1AA" : "#475569");

	return createTheme({
		palette: {
			mode,
			primary: {
				main: readToken("--accent", dark ? "#6366F1" : "#4F46E5"),
				light: readToken("--accent-hover", dark ? "#818CF8" : "#4338CA"),
				dark: dark ? "#4F46E5" : "#3730A3",
			},
			secondary: { main: textSecondary },
			success: { main: readToken("--success", dark ? "#22C55E" : "#15803D") },
			warning: { main: readToken("--warning", dark ? "#F59E0B" : "#A16207") },
			error: { main: readToken("--danger", dark ? "#EF4444" : "#B91C1C") },
			info: { main: readToken("--info", dark ? "#60A5FA" : "#1D4ED8") },
			background: { default: background, paper: surface },
			text: { primary: textPrimary, secondary: textSecondary },
			divider: border,
		},
		shape: { borderRadius: 6 },
		typography: {
			fontFamily: 'var(--sans), "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
			button: { textTransform: "none", fontWeight: 600 },
		},
		components: {
			MuiCssBaseline: {
				styleOverrides: {
					body: { backgroundColor: "var(--background)", color: "var(--text-primary)" },
					"::selection": { backgroundColor: "var(--primary-light)" },
				},
			},
			MuiPaper: {
				styleOverrides: {
					root: { backgroundImage: "none", backgroundColor: "var(--surface)", borderRadius: 6 },
				},
			},
			MuiButton: {
				styleOverrides: {
					root: { borderRadius: 6, minHeight: 40, boxShadow: "none", textTransform: "none" },
					contained: { boxShadow: "none" },
				},
			},
			MuiCard: { styleOverrides: { root: { borderRadius: 8, boxShadow: "none" } } },
			MuiOutlinedInput: {
				styleOverrides: {
					root: { backgroundColor: "var(--input-background)" },
					notchedOutline: { borderColor: "var(--border)" },
				},
			},
			MuiTableCell: {
				styleOverrides: {
					root: { borderBottomColor: "var(--border)" },
					head: { color: "var(--text-secondary)", backgroundColor: "var(--surface-secondary)", fontWeight: 600 },
				},
			},
		},
	});
}
