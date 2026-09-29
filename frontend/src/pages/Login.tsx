import {
	Alert,
	Box,
	CircularProgress,
	IconButton,
	InputAdornment,
	Typography,
} from "@mui/material";
import Visibility from "@mui/icons-material/Visibility";
import VisibilityOff from "@mui/icons-material/VisibilityOff";
import SecurityIcon from "@mui/icons-material/Security";
import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import Card from "../components/common/Card";
import Button from "../components/common/Button";
import Input from "../components/common/Input";
import { adminLogin, getGoogleAuthUrl } from "../api/auth";
import { useAuth } from "../context/AuthContext";

const GoogleIcon = () => (
	<svg width="20" height="20" viewBox="0 0 24 24" style={{ marginRight: 10 }}>
		<path
			fill="#4285F4"
			d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
		/>
		<path
			fill="#34A853"
			d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
		/>
		<path
			fill="#FBBC05"
			d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
		/>
		<path
			fill="#EA4335"
			d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
		/>
	</svg>
);

const Login = () => {
	const navigate = useNavigate();
	const [searchParams] = useSearchParams();
	const { isAuthenticated, login, isLoading: authLoading } = useAuth();

	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [showPassword, setShowPassword] = useState(false);
	const [loading, setLoading] = useState(false);
	const [googleLoading, setGoogleLoading] = useState(false);
	const [errorMessage, setErrorMessage] = useState<string | null>(null);

	// Check if already authenticated
	useEffect(() => {
		if (isAuthenticated && !authLoading) {
			navigate("/dashboard", { replace: true });
		}
	}, [isAuthenticated, authLoading, navigate]);

	// Extract query parameter errors (e.g. from Google OAuth callback failure)
	useEffect(() => {
		const errorParam = searchParams.get("error");
		if (errorParam) {
			setErrorMessage(errorParam);
		}
	}, [searchParams]);

	const handleGoogleLogin = async () => {
		try {
			setGoogleLoading(true);
			setErrorMessage(null);
			const authUrl = await getGoogleAuthUrl();
			window.location.href = authUrl;
		} catch (err: any) {
			console.error("Failed to initiate Google OAuth:", err);
			const detail =
				err.response?.data?.detail || "Google login service is currently unavailable.";
			setErrorMessage(detail);
			setGoogleLoading(false);
		}
	};

	const handleAdminLogin = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!email.trim() || !password) {
			setErrorMessage("Please enter both email and password.");
			return;
		}

		try {
			setLoading(true);
			setErrorMessage(null);
			const data = await adminLogin({
				email: email.trim(),
				password,
			});

			await login(data.access_token, data.user);
			navigate("/dashboard", { replace: true });
		} catch (err: any) {
			console.error("Admin login error:", err);
			const detail =
				err.response?.data?.detail || "Invalid credentials. Please verify and try again.";
			setErrorMessage(detail);
		} finally {
			setLoading(false);
		}
	};

	return (
		<div
			style={{
				minWidth: "100vw",
				minHeight: "100vh",
				display: "flex",
				alignItems: "center",
				justifyContent: "center",
				background: "linear-gradient(135deg, #0e0f14 0%, #151722 50%, #1b1626 100%)",
				padding: "24px",
				boxSizing: "border-box",
			}}
		>
			<Box sx={{ width: "100%", maxWidth: 440 }}>
				<Card>
					<Box sx={{ p: { xs: 2, sm: 3 } }}>
						{/* Brand & Title */}
						<Box sx={{ textAlign: "center", mb: 3 }}>
							<Box
								sx={{
									width: 48,
									height: 48,
									borderRadius: "14px",
									background: "linear-gradient(135deg, #a855f7 0%, #6366f1 100%)",
									color: "#fff",
									fontWeight: 700,
									fontSize: 24,
									display: "inline-flex",
									alignItems: "center",
									justifyContent: "center",
									boxShadow: "0 8px 24px rgba(168, 85, 247, 0.35)",
									mb: 1.5,
								}}
							>
								D
							</Box>
							<Typography
								variant="h5"
								sx={{
									fontWeight: 700,
									letterSpacing: "-0.5px",
									color: "var(--text-h, #ffffff)",
								}}
							>
								DocSentinel
							</Typography>
							<Typography
								variant="body2"
								sx={{ color: "var(--text, #9ca3af)", mt: 0.5 }}
							>
								AI-Powered Document Intelligence & Security
							</Typography>
						</Box>

						{/* Error Alert */}
						{errorMessage && (
							<Alert
								severity="error"
								onClose={() => setErrorMessage(null)}
								sx={{
									mb: 3,
									borderRadius: "10px",
									fontSize: "0.85rem",
								}}
							>
								{errorMessage}
							</Alert>
						)}

						{/* Google Login Section */}
						<Box sx={{ mb: 3 }}>
							<button
								type="button"
								onClick={handleGoogleLogin}
								disabled={googleLoading || loading}
								style={{
									width: "100%",
									display: "flex",
									alignItems: "center",
									justifyContent: "center",
									padding: "12px 18px",
									borderRadius: "12px",
									border: "1px solid rgba(255, 255, 255, 0.15)",
									background: "#ffffff",
									color: "#1f2937",
									fontWeight: 600,
									fontSize: "15px",
									cursor: googleLoading || loading ? "not-allowed" : "pointer",
									boxShadow: "0 2px 10px rgba(0, 0, 0, 0.08)",
									transition: "all 0.2s ease-in-out",
								}}
							>
								{googleLoading ? (
									<CircularProgress size={20} sx={{ color: "#4285F4" }} />
								) : (
									<>
										<GoogleIcon />
										<span>Continue with Google</span>
									</>
								)}
							</button>
						</Box>

						{/* Divider */}
						<Box
							sx={{
								display: "flex",
								alignItems: "center",
								my: 2.5,
								color: "var(--text, #6b7280)",
								"&::before, &::after": {
									content: '""',
									flex: 1,
									borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
								},
							}}
						>
							<Typography
								variant="caption"
								sx={{
									px: 1.5,
									fontWeight: 500,
									textTransform: "uppercase",
									letterSpacing: "0.5px",
									fontSize: "0.72rem",
								}}
							>
								or sign in as administrator
							</Typography>
						</Box>

						{/* Admin Login Form */}
						<form onSubmit={handleAdminLogin}>
							<Input
								label="Admin Email"
								type="email"
								value={email}
								onChange={(e) => setEmail(e.target.value)}
								required
								autoComplete="email"
								placeholder="admin@docsentinel.local"
								disabled={loading}
							/>

							<Input
								label="Password"
								type={showPassword ? "text" : "password"}
								value={password}
								onChange={(e) => setPassword(e.target.value)}
								required
								autoComplete="current-password"
								disabled={loading}
								InputProps={{
									endAdornment: (
										<InputAdornment position="end">
											<IconButton
												aria-label="toggle password visibility"
												onClick={() => setShowPassword((prev) => !prev)}
												edge="end"
												size="small"
												sx={{ color: "var(--text, #9ca3af)" }}
											>
												{showPassword ? <VisibilityOff /> : <Visibility />}
											</IconButton>
										</InputAdornment>
									),
								}}
							/>

							<Box sx={{ mt: 2.5 }}>
								<Button
									type="submit"
									fullWidth
									disabled={loading || googleLoading}
									variant="contained"
								>
									{loading ? (
										<CircularProgress size={22} sx={{ color: "#fff" }} />
									) : (
										"Sign In as Admin"
									)}
								</Button>
							</Box>
						</form>

						{/* Footer Security Note */}
						<Box
							sx={{
								mt: 3,
								pt: 2,
								borderTop: "1px solid rgba(255, 255, 255, 0.08)",
								display: "flex",
								alignItems: "center",
								justifyContent: "center",
								gap: 1,
								color: "var(--text, #6b7280)",
							}}
						>
							<SecurityIcon sx={{ fontSize: 16 }} />
							<Typography variant="caption">
								Role-Based Access Control Protected
							</Typography>
						</Box>
					</Box>
				</Card>
			</Box>
		</div>
	);
};

export default Login;