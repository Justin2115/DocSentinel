import { CircularProgress } from "@mui/material";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function AuthCallback() {
	const [searchParams] = useSearchParams();
	const navigate = useNavigate();
	const { login } = useAuth();
	const [statusText] = useState(() =>
		searchParams.get("token")
			? "Setting up your session..."
			: "Authenticating with Google..."
	);

	useEffect(() => {
		const token = searchParams.get("token");
		const error = searchParams.get("error");

		if (error) {
			navigate(`/login?error=${encodeURIComponent(error)}`, { replace: true });
			return;
		}

		if (token) {
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
		<div className="authCallbackPage" role="status" aria-live="polite">
			<CircularProgress size={48} />
			<h3 style={{ margin: 0, fontWeight: 500 }}>{statusText}</h3>
			<p>
				Please wait while we complete your sign-in to DocSentinel.
			</p>
		</div>
	);
}
