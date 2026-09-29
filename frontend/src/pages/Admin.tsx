import { useEffect, useState } from "react";
import {
	Alert,
	Avatar,
	Box,
	Chip,
	CircularProgress,
	IconButton,
	Paper,
	Table,
	TableBody,
	TableCell,
	TableContainer,
	TableHead,
	TableRow,
	Typography,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import PeopleIcon from "@mui/icons-material/People";
import SecurityIcon from "@mui/icons-material/Security";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";

import { getAdminUsers } from "../api/auth";
import type { User } from "../api/auth";
import { useAuth } from "../context/AuthContext";

export default function Admin() {
	const { user: currentUser } = useAuth();
	const [users, setUsers] = useState<User[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	const fetchUsers = async () => {
		try {
			setLoading(true);
			setError(null);
			const data = await getAdminUsers();
			setUsers(data);
		} catch (err: any) {
			console.error("Failed to fetch admin users:", err);
			const detail =
				err.response?.data?.detail || "Failed to load user management list.";
			setError(detail);
		} finally {
			setLoading(false);
		}
	};

	useEffect(() => {
		fetchUsers();
	}, []);

	const adminCount = users.filter((u) => u.role === "admin").length;
	const userCount = users.filter((u) => u.role === "user").length;

	return (
		<Box sx={{ p: { xs: 2, sm: 3 } }}>
			{/* Page Header */}
			<Box
				sx={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					mb: 3,
					flexWrap: "wrap",
					gap: 2,
				}}
			>
				<Box>
					<Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mb: 0.5 }}>
						<AdminPanelSettingsIcon
							sx={{ color: "var(--accent, #a855f7)", fontSize: 28 }}
						/>
						<Typography variant="h5" sx={{ fontWeight: 700 }}>
							Admin Management
						</Typography>
					</Box>
					<Typography variant="body2" sx={{ color: "var(--text, #9ca3af)" }}>
						Manage system users, view role assignments, and review security access.
					</Typography>
				</Box>

				<IconButton
					onClick={fetchUsers}
					disabled={loading}
					sx={{
						backgroundColor: "rgba(255, 255, 255, 0.05)",
						borderRadius: "10px",
						"&:hover": { backgroundColor: "rgba(255, 255, 255, 0.1)" },
					}}
					title="Refresh User List"
				>
					<RefreshIcon />
				</IconButton>
			</Box>

			{error && (
				<Alert severity="error" sx={{ mb: 3, borderRadius: "10px" }}>
					{error}
				</Alert>
			)}

			{/* Metric Cards */}
			<Box
				sx={{
					display: "grid",
					gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" },
					gap: 2,
					mb: 4,
				}}
			>
				<Paper
					sx={{
						p: 2.5,
						borderRadius: "16px",
						background: "rgba(255, 255, 255, 0.03)",
						border: "1px solid rgba(255, 255, 255, 0.08)",
					}}
				>
					<Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mb: 1 }}>
						<PeopleIcon sx={{ color: "#60a5fa" }} />
						<Typography variant="subtitle2" sx={{ color: "var(--text, #9ca3af)" }}>
							Total Registered Users
						</Typography>
					</Box>
					<Typography variant="h4" sx={{ fontWeight: 700 }}>
						{loading ? "..." : users.length}
					</Typography>
				</Paper>

				<Paper
					sx={{
						p: 2.5,
						borderRadius: "16px",
						background: "rgba(255, 255, 255, 0.03)",
						border: "1px solid rgba(255, 255, 255, 0.08)",
					}}
				>
					<Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mb: 1 }}>
						<SecurityIcon sx={{ color: "#a855f7" }} />
						<Typography variant="subtitle2" sx={{ color: "var(--text, #9ca3af)" }}>
							Administrators
						</Typography>
					</Box>
					<Typography variant="h4" sx={{ fontWeight: 700 }}>
						{loading ? "..." : adminCount}
					</Typography>
				</Paper>

				<Paper
					sx={{
						p: 2.5,
						borderRadius: "16px",
						background: "rgba(255, 255, 255, 0.03)",
						border: "1px solid rgba(255, 255, 255, 0.08)",
					}}
				>
					<Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mb: 1 }}>
						<CheckCircleIcon sx={{ color: "#34d399" }} />
						<Typography variant="subtitle2" sx={{ color: "var(--text, #9ca3af)" }}>
							Standard Users
						</Typography>
					</Box>
					<Typography variant="h4" sx={{ fontWeight: 700 }}>
						{loading ? "..." : userCount}
					</Typography>
				</Paper>
			</Box>

			{/* Users Table */}
			<TableContainer
				component={Paper}
				sx={{
					borderRadius: "16px",
					background: "rgba(255, 255, 255, 0.02)",
					border: "1px solid rgba(255, 255, 255, 0.08)",
					overflow: "hidden",
				}}
			>
				<Table>
					<TableHead sx={{ background: "rgba(255, 255, 255, 0.04)" }}>
						<TableRow>
							<TableCell sx={{ fontWeight: 600 }}>User</TableCell>
							<TableCell sx={{ fontWeight: 600 }}>Email</TableCell>
							<TableCell sx={{ fontWeight: 600 }}>Role</TableCell>
							<TableCell sx={{ fontWeight: 600 }}>Status</TableCell>
							<TableCell sx={{ fontWeight: 600 }}>Registered</TableCell>
							<TableCell sx={{ fontWeight: 600 }}>Last Login</TableCell>
						</TableRow>
					</TableHead>
					<TableBody>
						{loading ? (
							<TableRow>
								<TableCell colSpan={6} align="center" sx={{ py: 6 }}>
									<CircularProgress size={32} sx={{ color: "var(--accent, #a855f7)" }} />
								</TableCell>
							</TableRow>
						) : users.length === 0 ? (
							<TableRow>
								<TableCell colSpan={6} align="center" sx={{ py: 4, color: "#9ca3af" }}>
									No users found.
								</TableCell>
							</TableRow>
						) : (
							users.map((u) => {
								const initials = u.name
									? u.name
											.split(" ")
											.map((n) => n[0])
											.join("")
											.slice(0, 2)
											.toUpperCase()
									: "U";

								const isCurrentUser = currentUser?.id === u.id;

								return (
									<TableRow
										key={u.id}
										hover
										sx={{
											"&:last-child td, &:last-child th": { border: 0 },
										}}
									>
										<TableCell>
											<Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
												<Avatar
													src={u.profile_picture || undefined}
													sx={{
														width: 36,
														height: 36,
														bgcolor:
															u.role === "admin" ? "#a855f7" : "#4f46e5",
														fontSize: "14px",
														fontWeight: 600,
													}}
												>
													{initials}
												</Avatar>
												<Box>
													<Typography variant="body2" sx={{ fontWeight: 600 }}>
														{u.name}
														{isCurrentUser && (
															<Chip
																label="You"
																size="small"
																sx={{
																	ml: 1,
																	height: 18,
																	fontSize: "0.65rem",
																	bgcolor: "rgba(168, 85, 247, 0.2)",
																	color: "#c084fc",
																}}
															/>
														)}
													</Typography>
													<Typography
														variant="caption"
														sx={{ color: "var(--text, #9ca3af)" }}
													>
														ID: #{u.id}
													</Typography>
												</Box>
											</Box>
										</TableCell>
										<TableCell>
											<Typography variant="body2">{u.email}</Typography>
										</TableCell>
										<TableCell>
											<Chip
												label={u.role.toUpperCase()}
												size="small"
												sx={{
													fontWeight: 700,
													fontSize: "0.7rem",
													borderRadius: "6px",
													backgroundColor:
														u.role === "admin"
															? "rgba(168, 85, 247, 0.15)"
															: "rgba(59, 130, 246, 0.15)",
													color: u.role === "admin" ? "#c084fc" : "#60a5fa",
													border:
														u.role === "admin"
															? "1px solid rgba(168, 85, 247, 0.3)"
															: "1px solid rgba(59, 130, 246, 0.3)",
												}}
											/>
										</TableCell>
										<TableCell>
											<Chip
												label={u.is_active ? "Active" : "Inactive"}
												size="small"
												color={u.is_active ? "success" : "default"}
												variant="outlined"
												sx={{ height: 22, fontSize: "0.7rem" }}
											/>
										</TableCell>
										<TableCell>
											<Typography variant="caption" sx={{ color: "var(--text, #9ca3af)" }}>
												{u.created_at
													? new Date(u.created_at).toLocaleDateString()
													: "N/A"}
											</Typography>
										</TableCell>
										<TableCell>
											<Typography variant="caption" sx={{ color: "var(--text, #9ca3af)" }}>
												{u.last_login
													? new Date(u.last_login).toLocaleString()
													: "Never"}
											</Typography>
										</TableCell>
									</TableRow>
								);
							})
						)}
					</TableBody>
				</Table>
			</TableContainer>
		</Box>
	);
}
