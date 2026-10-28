import {
	Alert,
	Box,
	CircularProgress,
	IconButton,
	InputAdornment,
	Typography,
} from "@mui/material";
import { isAxiosError } from "axios";
import Visibility from "@mui/icons-material/Visibility";
import VisibilityOff from "@mui/icons-material/VisibilityOff";
import SecurityIcon from "@mui/icons-material/Security";
import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import Card from "../components/common/Card";
import Button from "../components/common/Button";
import Input from "../components/common/Input";
import ThemeSwitcher from "../components/common/ThemeSwitcher";
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
	const [errorMessage, setErrorMessage] = useState<string | null>(
		() => searchParams.get("error")
	);

	// Check if already authenticated
	useEffect(() => {
		if (isAuthenticated && !authLoading) {
			navigate("/dashboard", { replace: true });
		}
	}, [isAuthenticated, authLoading, navigate]);

	const handleGoogleLogin = async () => {
		try {
			setGoogleLoading(true);
			setErrorMessage(null);
			const authUrl = await getGoogleAuthUrl();
			window.location.href = authUrl;
		} catch (err: unknown) {
			console.error("Failed to initiate Google OAuth:", err);
			const detail =
				isAxiosError<{ detail?: string }>(err) && err.response?.data?.detail
					? err.response.data.detail
					: "Google login service is currently unavailable.";
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
		} catch (err: unknown) {
			console.error("Admin login error:", err);
			const detail =
				isAxiosError<{ detail?: string }>(err) && err.response?.data?.detail
					? err.response.data.detail
					: "Invalid credentials. Please verify and try again.";
			setErrorMessage(detail);
		} finally {
			setLoading(false);
		}
	};

	return (
		<div className="authPage">
			<Box sx={{ width: "100%", maxWidth: 440 }}>
				<Card>
					<Box sx={{ p: { xs: 2, sm: 3 } }}>
						<Box sx={{ display: "flex", justifyContent: "flex-end", mb: 2 }}>
							<ThemeSwitcher />
						</Box>
						{/* Brand & Title */}
						<Box sx={{ textAlign: "center", mb: 3 }}>
							<Box
								className="authBrandMark"
								sx={{
									width: 48,
									height: 48,
									borderRadius: "8px",
									background: "var(--primary)",
									color: "#fff",
									fontWeight: 700,
									fontSize: 24,
									display: "inline-flex",
									alignItems: "center",
									justifyContent: "center",
									boxShadow: "none",
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
								color: "var(--text-primary)",
								}}
							>
								DocSentinel
							</Typography>
							<Typography
								variant="body2"
								sx={{ color: "var(--text-secondary)", mt: 0.5 }}
							>
								Secure search and review for your documents
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
													minHeight: "44px",
													borderRadius: "6px",
													border: "1px solid var(--border)",
													background: "var(--surface)",
													color: "var(--text-primary)",
									fontWeight: 600,
									fontSize: "15px",
									cursor: googleLoading || loading ? "not-allowed" : "pointer",
								boxShadow: "none",
									transition: "all 0.2s ease-in-out",
								}}
							>
								{googleLoading ? (
									<CircularProgress size={20} sx={{ color: "var(--primary)" }} />
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
								color: "var(--text)",
								"&::before, &::after": {
									content: '""',
									flex: 1,
													borderBottom: "1px solid var(--border)",
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
									fontSize: "0.8rem",
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
													aria-label={showPassword ? "Hide password" : "Show password"}
													aria-pressed={showPassword}
												onClick={() => setShowPassword((prev) => !prev)}
												edge="end"
												size="small"
												sx={{ color: "var(--text)" }}
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
								borderTop: "1px solid var(--border)",
								display: "flex",
								alignItems: "center",
								justifyContent: "center",
								gap: 1,
								color: "var(--text)",
							}}
						>
							<SecurityIcon sx={{ fontSize: 16 }} />
							<Typography variant="caption">
								Access follows your assigned role
							</Typography>
						</Box>
					</Box>
				</Card>
			</Box>
		</div>
	);
};

export default Login;
