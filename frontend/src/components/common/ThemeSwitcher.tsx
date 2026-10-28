import { DarkMode, LightMode } from "@mui/icons-material";
import { useThemeMode } from "../../context/useThemeMode";

export default function ThemeSwitcher({ showLabels = true }: { showLabels?: boolean }) {
	const { mode, setMode } = useThemeMode();

	return (
		<div className="themeSwitcher" role="group" aria-label="Color theme">
			<button
				type="button"
				className={`themeOption${mode === "light" ? " active" : ""}`}
				onClick={() => setMode("light")}
				title="Light theme"
				aria-label="Light theme"
				aria-pressed={mode === "light"}
			>
				<LightMode />
				{showLabels && <span>Light</span>}
			</button>
			<button
				type="button"
				className={`themeOption${mode === "dark" ? " active" : ""}`}
				onClick={() => setMode("dark")}
				title="Dark theme"
				aria-label="Dark theme"
				aria-pressed={mode === "dark"}
			>
				<DarkMode />
				{showLabels && <span>Dark</span>}
			</button>
		</div>
	);
}
