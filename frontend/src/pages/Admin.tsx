import { useEffect, useState } from "react";
import {
	Alert,
	Avatar,
	Box,
	Chip,
	CircularProgress,
	IconButton,
	MenuItem,
	Paper,
	Select,
	Snackbar,
	Table,
	TableBody,
	TableCell,
	TableContainer,
	TableHead,
	TableRow,
	Tooltip,
	Typography,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import PeopleIcon from "@mui/icons-material/People";
import SecurityIcon from "@mui/icons-material/Security";
import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import FactCheckIcon from "@mui/icons-material/FactCheck";

import { getAdminUsers, updateAdminUser } from "../api/auth";
import type { User } from "../api/auth";
import { useAuth } from "../context/AuthContext";

export default function Admin() {
	const { user: currentUser } = useAuth();
	const [users, setUsers] = useState<User[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [successMsg, setSuccessMsg] = useState<string | null>(null);
	const [updatingUserId, setUpdatingUserId] = useState<number | null>(null);

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

	const normalizeRole = (role: string) => {
		const r = (role || "").toUpperCase();
		if (r === "ADMIN" || r === "ADMINISTRATOR") return "ADMIN";
		if (r === "UPLOAD_CHECKER" || r === "CHECKER") return "UPLOAD_CHECKER";
		return "UPLOAD_MAKER";
	};

	const handleRoleChange = async (userId: number, newRole: string) => {
		try {
			setUpdatingUserId(userId);
			setError(null);
			const updated = await updateAdminUser(userId, { role: newRole });
			setUsers((prev) =>
				prev.map((u) => (u.id === userId ? { ...u, role: updated.role } : u))
			);
			setSuccessMsg(`Role for user #${userId} updated to ${newRole}`);
		} catch (err: any) {
			console.error("Failed to update role:", err);
			setError(err.response?.data?.detail || "Failed to update user role");
		} finally {
			setUpdatingUserId(null);
		}
	};

	const handleDepartmentChange = async (userId: number, newDept: string) => {
		try {
			setUpdatingUserId(userId);
			setError(null);
			const deptValue = newDept === "NONE" || !newDept ? null : newDept;
			const updated = await updateAdminUser(userId, { department: deptValue });
			setUsers((prev) =>
				prev.map((u) => (u.id === userId ? { ...u, department: updated.department } : u))
			);
			setSuccessMsg(`Department for user #${userId} updated to ${deptValue || "None"}`);
		} catch (err: any) {
			console.error("Failed to update department:", err);
			setError(err.response?.data?.detail || "Failed to update user department");
		} finally {
			setUpdatingUserId(null);
		}
	};

	const adminCount = users.filter((u) => normalizeRole(u.role) === "ADMIN").length;
	const makerCount = users.filter((u) => normalizeRole(u.role) === "UPLOAD_MAKER").length;
	const checkerCount = users.filter((u) => normalizeRole(u.role) === "UPLOAD_CHECKER").length;

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
				<Alert severity="error" sx={{ mb: 3, borderRadius: "10px" }} onClose={() => setError(null)}>
					{error}
				</Alert>
			)}

			{/* Metric Cards */}
			<Box
				sx={{
					display: "grid",
					gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", lg: "repeat(4, 1fr)" },
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
						<CloudUploadIcon sx={{ color: "#38bdf8" }} />
						<Typography variant="subtitle2" sx={{ color: "var(--text, #9ca3af)" }}>
							Upload Makers
						</Typography>
					</Box>
					<Typography variant="h4" sx={{ fontWeight: 700 }}>
						{loading ? "..." : makerCount}
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
						<FactCheckIcon sx={{ color: "#34d399" }} />
						<Typography variant="subtitle2" sx={{ color: "var(--text, #9ca3af)" }}>
							Upload Checkers
						</Typography>
					</Box>
					<Typography variant="h4" sx={{ fontWeight: 700 }}>
						{loading ? "..." : checkerCount}
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
							<TableCell sx={{ fontWeight: 600 }}>Department</TableCell>
							<TableCell sx={{ fontWeight: 600 }}>Status</TableCell>
							<TableCell sx={{ fontWeight: 600 }}>Registered</TableCell>
							<TableCell sx={{ fontWeight: 600 }}>Last Login</TableCell>
						</TableRow>
					</TableHead>
					<TableBody>
						{loading ? (
							<TableRow>
								<TableCell colSpan={7} align="center" sx={{ py: 6 }}>
									<CircularProgress size={32} sx={{ color: "var(--accent, #a855f7)" }} />
								</TableCell>
							</TableRow>
						) : users.length === 0 ? (
							<TableRow>
								<TableCell colSpan={7} align="center" sx={{ py: 4, color: "#9ca3af" }}>
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
								const roleVal = normalizeRole(u.role);

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
															roleVal === "ADMIN"
																? "#a855f7"
																: roleVal === "UPLOAD_CHECKER"
																	? "#059669"
																	: "#2563eb",
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
											<Tooltip
												title={
													isCurrentUser
														? "You cannot demote or change your own role"
														: "Select user role"
												}
											>
												<span>
													<Select
														size="small"
														value={roleVal}
														disabled={isCurrentUser || updatingUserId === u.id}
														onChange={(e) => handleRoleChange(u.id, e.target.value)}
														sx={{
															fontSize: "0.75rem",
															height: 32,
															fontWeight: 700,
															borderRadius: "8px",
															color:
																roleVal === "ADMIN"
																	? "#c084fc"
																	: roleVal === "UPLOAD_CHECKER"
																		? "#34d399"
																		: "#60a5fa",
															backgroundColor: "rgba(255, 255, 255, 0.05)",
															"& .MuiOutlinedInput-notchedOutline": {
																borderColor: "rgba(255, 255, 255, 0.15)",
															},
														}}
													>
														<MenuItem value="ADMIN">ADMIN</MenuItem>
														<MenuItem value="UPLOAD_MAKER">UPLOAD_MAKER</MenuItem>
														<MenuItem value="UPLOAD_CHECKER">UPLOAD_CHECKER</MenuItem>
													</Select>
												</span>
											</Tooltip>
										</TableCell>
										<TableCell>
											<Select
												size="small"
												value={u.department?.toUpperCase() || "NONE"}
												disabled={updatingUserId === u.id}
												onChange={(e) => handleDepartmentChange(u.id, e.target.value)}
												sx={{
													fontSize: "0.75rem",
													height: 32,
													fontWeight: 600,
													borderRadius: "8px",
													color: u.department ? "#fbbf24" : "var(--text, #9ca3af)",
													backgroundColor: "rgba(255, 255, 255, 0.05)",
													"& .MuiOutlinedInput-notchedOutline": {
														borderColor: "rgba(255, 255, 255, 0.15)",
													},
												}}
											>
												<MenuItem value="NONE"><em>None</em></MenuItem>
												<MenuItem value="HR">HR</MenuItem>
												<MenuItem value="FINANCE">FINANCE</MenuItem>
												<MenuItem value="LEGAL">LEGAL</MenuItem>
												<MenuItem value="OPERATIONS">OPERATIONS</MenuItem>
												<MenuItem value="IT">IT</MenuItem>
											</Select>
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

			<Snackbar
				open={Boolean(successMsg)}
				autoHideDuration={4000}
				onClose={() => setSuccessMsg(null)}
				anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
			>
				<Alert severity="success" onClose={() => setSuccessMsg(null)}>
					{successMsg}
				</Alert>
			</Snackbar>
		</Box>
	);
}
