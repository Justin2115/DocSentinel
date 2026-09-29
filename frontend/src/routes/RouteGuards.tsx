import { Box, CircularProgress } from "@mui/material";
import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export const ProtectedRoute: React.FC<{ children: React.ReactElement }> = ({ children }) => {
	const { isAuthenticated, isLoading } = useAuth();
	const location = useLocation();

	if (isLoading) {
		return (
			<Box
				sx={{
					width: "100vw",
					height: "100vh",
					display: "flex",
					alignItems: "center",
					justifyContent: "center",
					background: "var(--bg, #0f1015)",
				}}
			>
				<CircularProgress size={40} sx={{ color: "var(--accent, #a855f7)" }} />
			</Box>
		);
	}

	if (!isAuthenticated) {
		return <Navigate to="/login" state={{ from: location }} replace />;
	}

	return children;
};

export const AdminRoute: React.FC<{ children: React.ReactElement }> = ({ children }) => {
	const { isAuthenticated, isAdmin, isLoading } = useAuth();
	const location = useLocation();

	if (isLoading) {
		return (
			<Box
				sx={{
					width: "100vw",
					height: "100vh",
					display: "flex",
					alignItems: "center",
					justifyContent: "center",
					background: "var(--bg, #0f1015)",
				}}
			>
				<CircularProgress size={40} sx={{ color: "var(--accent, #a855f7)" }} />
			</Box>
		);
	}

	if (!isAuthenticated) {
		return <Navigate to="/login" state={{ from: location }} replace />;
	}

	if (!isAdmin) {
		// Normal users cannot access admin routes
		return <Navigate to="/dashboard" replace />;
	}

	return children;
};
