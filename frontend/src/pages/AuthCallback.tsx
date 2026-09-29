import { CircularProgress } from "@mui/material";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function AuthCallback() {
	const [searchParams] = useSearchParams();
	const navigate = useNavigate();
	const { login } = useAuth();
	const [statusText, setStatusText] = useState("Authenticating with Google...");

	useEffect(() => {
		const token = searchParams.get("token");
		const error = searchParams.get("error");

		if (error) {
			navigate(`/login?error=${encodeURIComponent(error)}`, { replace: true });
			return;
		}

		if (token) {
			setStatusText("Setting up your session...");
			login(token)
				.then(() => {
					navigate("/dashboard", { replace: true });
				})
				.catch((err) => {
					console.error("Failed to complete Google OAuth login:", err);
					navigate("/login?error=Failed%20to%20initialize%20user%20session", { replace: true });
				});
		} else {
			navigate("/login?error=Invalid%20or%20missing%20authentication%20token", { replace: true });
		}
	}, [searchParams, login, navigate]);

	return (
		<div
			style={{
				width: "100vw",
				height: "100vh",
				display: "flex",
				flexDirection: "column",
				alignItems: "center",
				justifyContent: "center",
				background: "var(--bg, #0f1015)",
				color: "var(--text-h, #ffffff)",
				gap: "20px",
			}}
		>
			<CircularProgress sx={{ color: "var(--accent, #a855f7)" }} size={48} />
			<h3 style={{ margin: 0, fontWeight: 500 }}>{statusText}</h3>
			<p style={{ color: "var(--text, #9ca3af)", fontSize: "14px" }}>
				Please wait while we complete your sign-in to DocSentinel.
			</p>
		</div>
	);
}
