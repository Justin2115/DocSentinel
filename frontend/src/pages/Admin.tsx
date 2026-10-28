import { useEffect, useState, useMemo } from "react";
import {
	Alert,
	Avatar,
	Box,
	Button,
	Chip,
	CircularProgress,
	Dialog,
	DialogActions,
	DialogContent,
	DialogContentText,
	DialogTitle,
	IconButton,
	MenuItem,
	Paper,
	Select,
	Snackbar,
	Tab,
	Table,
	TableBody,
	TableCell,
	TableContainer,
	TableHead,
	TablePagination,
	TableRow,
	Tabs,
	TextField,
	Typography,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import PeopleIcon from "@mui/icons-material/People";
import HistoryIcon from "@mui/icons-material/History";
import DeleteIcon from "@mui/icons-material/Delete";
import PersonAddIcon from "@mui/icons-material/PersonAdd";
import SearchIcon from "@mui/icons-material/Search";
import FilterListIcon from "@mui/icons-material/FilterList";

import {
	getAdminUsers,
	updateAdminUser,
	inviteAdminUser,
	deleteAdminUser,
	permanentlyDeleteAdminUser,
	getActivityLogs,
} from "../api/auth";
import type {
	User,
	UserInvitePayload,
	ActivityLogItem,
	ActivityLogFilterParams,
} from "../api/auth";
import { useAuth } from "../context/AuthContext";

export default function Admin() {
	const { user: currentUser, isSuperAdmin, isDeptAdmin } = useAuth();
	const [currentTab, setCurrentTab] = useState<number>(0);

	// ==========================================
	// USER MANAGEMENT STATE
	// ==========================================
	const [users, setUsers] = useState<User[]>([]);
	const [usersLoading, setUsersLoading] = useState(true);
	const [selectedDeptFilter, setSelectedDeptFilter] = useState<string>("ALL");
	const [updatingUserId, setUpdatingUserId] = useState<number | null>(null);

	// Invite dialog
	const [inviteOpen, setInviteOpen] = useState(false);
	const [inviteForm, setInviteForm] = useState<UserInvitePayload>({
		name: "",
		email: "",
		role: "DEPARTMENT_MAKER",
		department: isDeptAdmin ? currentUser?.department || "HR" : "HR",
		password: "",
	});
	const [inviteSubmitting, setInviteSubmitting] = useState(false);

	// Delete confirmation dialog
	const [deleteTargetUser, setDeleteTargetUser] = useState<User | null>(null);
	const [deletePermanent, setDeletePermanent] = useState(false);
	const [deleting, setDeleting] = useState(false);

	// ==========================================
	// ACTIVITY LOGS STATE
	// ==========================================
	const [activityLogs, setActivityLogs] = useState<ActivityLogItem[]>([]);
	const [logsTotal, setLogsTotal] = useState(0);
	const [logsLoading, setLogsLoading] = useState(false);
	const [logPage, setLogPage] = useState(0);
	const [logPageSize, setLogPageSize] = useState(20);
	const [logSearch, setLogSearch] = useState("");
	const [logAction, setLogAction] = useState("ALL");
	const [logStatus, setLogStatus] = useState("ALL");
	const [logDept, setLogDept] = useState("ALL");
	const [logStartDate, setLogStartDate] = useState("");
	const [logEndDate, setLogEndDate] = useState("");
	const [selectedLogDetail, setSelectedLogDetail] = useState<ActivityLogItem | null>(null);

	// Feedback
	const [error, setError] = useState<string | null>(null);
	const [successMsg, setSuccessMsg] = useState<string | null>(null);

	// ==========================================
	// FETCH USERS
	// ==========================================
	const fetchUsers = async () => {
		try {
			setUsersLoading(true);
			setError(null);
			const data = await getAdminUsers();
			setUsers(data);
		} catch (err: any) {
			console.error("Failed to fetch admin users:", err);
			setError(err.response?.data?.detail || "Failed to load user management list.");
		} finally {
			setUsersLoading(false);
		}
	};

	// ==========================================
	// FETCH ACTIVITY LOGS
	// ==========================================
	const fetchLogs = async (page = logPage, pageSize = logPageSize) => {
		try {
			setLogsLoading(true);
			setError(null);

			const params: ActivityLogFilterParams = {
				page: page + 1, // backend is 1-indexed
				page_size: pageSize,
				search: logSearch.trim() || undefined,
				action: logAction !== "ALL" ? logAction : undefined,
				status: logStatus !== "ALL" ? logStatus : undefined,
				department: isSuperAdmin
					? logDept !== "ALL"
						? logDept
						: undefined
					: currentUser?.department || undefined,
				start_date: logStartDate || undefined,
				end_date: logEndDate || undefined,
			};

			const data = await getActivityLogs(params);
			setActivityLogs(data.items);
			setLogsTotal(data.total);
		} catch (err: any) {
			console.error("Failed to fetch activity logs:", err);
			setError(err.response?.data?.detail || "Failed to load activity logs.");
		} finally {
			setLogsLoading(false);
		}
	};

	useEffect(() => {
		if (currentTab === 0) {
			fetchUsers();
		} else if (currentTab === 1) {
			fetchLogs(0, logPageSize);
		}
	}, [currentTab]);

	// ==========================================
	// USER MANAGEMENT ACTIONS
	// ==========================================
	const normalizeRole = (role: string) => {
		const r = (role || "").toUpperCase().replace(/\s+/g, "_");
		if (r === "SUPER_ADMIN" || r === "ADMIN") return "SUPER_ADMIN";
		if (r === "DEPARTMENT_ADMIN" || r === "DEPT_ADMIN") return "DEPARTMENT_ADMIN";
		if (r === "DEPARTMENT_CHECKER" || r === "UPLOAD_CHECKER" || r === "CHECKER")
			return "DEPARTMENT_CHECKER";
		return "DEPARTMENT_MAKER";
	};

	const roleLabel = (role: string) => {
		switch (normalizeRole(role)) {
			case "SUPER_ADMIN":
				return "Super Admin";
			case "DEPARTMENT_ADMIN":
				return "Department Admin";
			case "DEPARTMENT_CHECKER":
				return "Department Checker";
			case "DEPARTMENT_MAKER":
				return "Department Maker";
			default:
				return role;
		}
	};

	const handleRoleChange = async (userId: number, newRole: string) => {
		try {
			setUpdatingUserId(userId);
			setError(null);
			const targetUser = users.find((u) => u.id === userId);
			if (newRole !== "SUPER_ADMIN" && (!targetUser?.department || targetUser.department === "NONE")) {
				setError("Please assign a valid department before setting a non-Super-Admin role.");
				return;
			}
			const updated = await updateAdminUser(userId, { role: newRole });
			setUsers((prev) =>
				prev.map((u) => (u.id === userId ? { ...u, role: updated.role } : u))
			);
			setSuccessMsg(`Role for user #${userId} updated to ${roleLabel(newRole)}`);
		} catch (err: any) {
			console.error("Failed to update role:", err);
			setError(err.response?.data?.detail || "Failed to update user role");
		} finally {
			setUpdatingUserId(null);
		}
	};

	const handleDepartmentChange = async (userId: number, deptValue: string) => {
		try {
			setUpdatingUserId(userId);
			setError(null);
			const targetDept = deptValue === "NONE" || deptValue === "" ? null : deptValue;
			const updated = await updateAdminUser(userId, { department: targetDept });
			setUsers((prev) =>
				prev.map((u) => (u.id === userId ? { ...u, department: updated.department } : u))
			);
			setSuccessMsg(`Department for user #${userId} updated to ${updated.department || "Cross-Department"}`);
		} catch (err: any) {
			console.error("Failed to update department:", err);
			setError(err.response?.data?.detail || "Failed to update user department");
		} finally {
			setUpdatingUserId(null);
		}
	};

	const handleToggleActive = async (user: User) => {
		try {
			setUpdatingUserId(user.id);
			setError(null);
			const updated = await updateAdminUser(user.id, { is_active: !user.is_active });
			setUsers((prev) =>
				prev.map((u) => (u.id === user.id ? { ...u, is_active: updated.is_active } : u))
			);
			setSuccessMsg(
				`User #${user.id} has been ${updated.is_active ? "activated" : "deactivated"}.`
			);
		} catch (err: any) {
			console.error("Failed to toggle status:", err);
			setError(err.response?.data?.detail || "Failed to update user status");
		} finally {
			setUpdatingUserId(null);
		}
	};

	const handleConfirmDelete = async () => {
		if (!deleteTargetUser) return;
		try {
			setDeleting(true);
			setError(null);
			if (deletePermanent) {
				await permanentlyDeleteAdminUser(deleteTargetUser.id);
				setUsers((prev) => prev.filter((u) => u.id !== deleteTargetUser.id));
				setSuccessMsg(
					`User #${deleteTargetUser.id} (${deleteTargetUser.email}) permanently removed.`
				);
			} else {
				await deleteAdminUser(deleteTargetUser.id);
				setUsers((prev) =>
					prev.map((u) =>
						u.id === deleteTargetUser.id ? { ...u, is_active: false } : u
					)
				);
				setSuccessMsg(`User #${deleteTargetUser.id} has been deactivated.`);
			}
			setDeleteTargetUser(null);
		} catch (err: any) {
			console.error("Failed to delete user:", err);
			setError(err.response?.data?.detail || "Failed to delete user.");
		} finally {
			setDeleting(false);
		}
	};

	const handleInviteSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		try {
			setInviteSubmitting(true);
			setError(null);

			const selectedDept = isDeptAdmin
				? currentUser?.department || null
				: inviteForm.role === "SUPER_ADMIN"
				? inviteForm.department || null
				: inviteForm.department;

			if (inviteForm.role !== "SUPER_ADMIN" && !selectedDept) {
				setError("A valid department must be selected for Department Admin, Checker, and Maker roles.");
				setInviteSubmitting(false);
				return;
			}

			const payload: UserInvitePayload = {
				name: inviteForm.name.trim(),
				email: inviteForm.email.trim(),
				role: inviteForm.role,
				department: selectedDept,
				password: inviteForm.password?.trim() || undefined,
			};
			const created = await inviteAdminUser(payload);
			setUsers((prev) => [...prev, created]);
			if (inviteForm.password?.trim()) {
				setSuccessMsg(`User ${created.email} created with active password credentials.`);
			} else {
				setSuccessMsg(`User ${created.email} invited for Google OAuth login.`);
			}
			setInviteOpen(false);
			setInviteForm({
				name: "",
				email: "",
				role: "DEPARTMENT_MAKER",
				department: isDeptAdmin ? currentUser?.department || "HR" : "HR",
				password: "",
			});
		} catch (err: any) {
			console.error("Failed to invite user:", err);
			setError(err.response?.data?.detail || "Failed to invite user.");
		} finally {
			setInviteSubmitting(false);
		}
	};

	// Filtered users for Super Admin department filter
	const displayedUsers = useMemo(() => {
		if (!isSuperAdmin || selectedDeptFilter === "ALL") {
			return users;
		}
		return users.filter(
			(u) => (u.department || "").toUpperCase() === selectedDeptFilter.toUpperCase()
		);
	}, [users, isSuperAdmin, selectedDeptFilter]);

	// Metrics
	const superAdminCount = users.filter((u) => normalizeRole(u.role) === "SUPER_ADMIN").length;
	const deptAdminCount = users.filter((u) => normalizeRole(u.role) === "DEPARTMENT_ADMIN").length;
	const checkerCount = users.filter((u) => normalizeRole(u.role) === "DEPARTMENT_CHECKER").length;
	const makerCount = users.filter((u) => normalizeRole(u.role) === "DEPARTMENT_MAKER").length;

	return (
		<Box sx={{ p: { xs: 2, sm: 3 }, color: "var(--text-primary)" }}>
			{/* Header */}
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
				<Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
					<AdminPanelSettingsIcon
						sx={{ color: "var(--accent)", fontSize: 32 }}
					/>
					<Box>
						<Typography variant="h5" sx={{ fontWeight: 700, color: "var(--text-primary)" }}>
							System Administration
						</Typography>
						<Typography variant="body2" sx={{ color: "var(--text-secondary)" }}>
							{isSuperAdmin
								? "System-wide administration, RBAC governance, and security audit logs"
								: `Department Admin console — ${currentUser?.department || "Assigned"} Department`}
						</Typography>
					</Box>
				</Box>

				<Box sx={{ display: "flex", gap: 1 }}>
					<Button
						variant="contained"
						startIcon={<PersonAddIcon />}
						onClick={() => setInviteOpen(true)}
						sx={{
						bgcolor: "var(--primary)",
						"&:hover": { bgcolor: "var(--primary-hover)" },
							borderRadius: "8px",
							textTransform: "none",
							fontWeight: 600,
						}}
					>
						Add User
					</Button>
					<IconButton
						onClick={() => (currentTab === 0 ? fetchUsers() : fetchLogs(logPage, logPageSize))}
						sx={{
							border: "1px solid var(--border)",
							borderRadius: "8px",
							color: "var(--text-secondary)",
						}}
						title="Refresh"
					>
						<RefreshIcon />
					</IconButton>
				</Box>
			</Box>

			{/* Navigation Tabs */}
			<Tabs
				value={currentTab}
				onChange={(_, v) => setCurrentTab(v)}
				sx={{
					mb: 3,
					borderBottom: "1px solid var(--border)",
					"& .MuiTab-root": {
						textTransform: "none",
						fontWeight: 600,
						fontSize: "0.95rem",
						color: "var(--text-secondary)",
						"&.Mui-selected": { color: "var(--accent)" },
					},
					"& .MuiTabs-indicator": { backgroundColor: "var(--accent)" },
				}}
			>
				<Tab icon={<PeopleIcon />} iconPosition="start" label="Users & Roles" />
				<Tab icon={<HistoryIcon />} iconPosition="start" label="Activity Logs" />
			</Tabs>

			{/* Error Banner */}
			{error && (
				<Alert severity="error" sx={{ mb: 3, borderRadius: "10px" }} onClose={() => setError(null)}>
					{error}
				</Alert>
			)}

			{/* ======================================================== */}
			{/* TAB 0: USERS & ROLES */}
			{/* ======================================================== */}
			{currentTab === 0 && (
				<>
					<Box className="adminRoleSummary" aria-label="Users by role">
						<span>Users by role</span>
						<div>
							{isSuperAdmin && <span><strong>{usersLoading ? "…" : superAdminCount}</strong> Super admins</span>}
							<span><strong>{usersLoading ? "…" : deptAdminCount}</strong> Department admins</span>
							<span><strong>{usersLoading ? "…" : checkerCount}</strong> Checkers</span>
							<span><strong>{usersLoading ? "…" : makerCount}</strong> Makers</span>
						</div>
					</Box>

					{/* Department filter bar (Super Admin only) */}
					{isSuperAdmin && (
						<Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mb: 2 }}>
							<FilterListIcon sx={{ color: "var(--text-secondary)", fontSize: 20 }} />
							<Typography variant="body2" sx={{ color: "var(--text-secondary)", fontWeight: 600 }}>
								Department Scope:
							</Typography>
							<Select
								size="small"
								value={selectedDeptFilter}
								onChange={(e) => setSelectedDeptFilter(e.target.value)}
								sx={{
									fontSize: "0.8rem",
									height: 32,
									borderRadius: "8px",
									backgroundColor: "var(--surface)",
									color: "var(--text-primary)",
									border: "1px solid var(--border)",
								}}
							>
								<MenuItem value="ALL">All Departments</MenuItem>
								<MenuItem value="HR">HR</MenuItem>
								<MenuItem value="FINANCE">Finance</MenuItem>
								<MenuItem value="LEGAL">Legal</MenuItem>
								<MenuItem value="OPERATIONS">Operations</MenuItem>
								<MenuItem value="IT">IT</MenuItem>
							</Select>
						</Box>
					)}

					{/* Users Table */}
					<TableContainer
						component={Paper}
						sx={{
							borderRadius: "14px",
							background: "var(--surface)",
							color: "var(--text-primary)",
							border: "1px solid var(--border)",
							overflow: "hidden",
						}}
					>
						<Table size="small">
							<TableHead sx={{ background: "var(--surface-secondary)" }}>
								<TableRow>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)" }}>User</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)" }}>Email &amp; Auth</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)" }}>Role</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)" }}>Department</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)" }}>Status</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)" }}>Registered</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)" }}>Last Login</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)", textAlign: "right" }}>Actions</TableCell>
								</TableRow>
							</TableHead>
							<TableBody>
								{usersLoading ? (
									<TableRow>
										<TableCell colSpan={8} align="center" sx={{ py: 6 }}>
											<CircularProgress size={30} sx={{ color: "var(--accent)" }} />
										</TableCell>
									</TableRow>
								) : displayedUsers.length === 0 ? (
									<TableRow>
										<TableCell colSpan={8} align="center" sx={{ py: 4, color: "var(--text-secondary)" }}>
											No users found for this department scope.
										</TableCell>
									</TableRow>
								) : (
									displayedUsers.map((u) => {
										const initials = u.name
											? u.name
													.split(" ")
													.map((n) => n[0])
													.join("")
													.slice(0, 2)
													.toUpperCase()
											: "U";

										const isCurrent = currentUser?.id === u.id;
										const roleVal = normalizeRole(u.role);
										const isTargetSuperAdmin = roleVal === "SUPER_ADMIN";

										// Role options based on authenticated user's permissions
										let allowedRoles = ["DEPARTMENT_CHECKER", "DEPARTMENT_MAKER"];
										if (isSuperAdmin) {
											allowedRoles = [
												"SUPER_ADMIN",
												"DEPARTMENT_ADMIN",
												"DEPARTMENT_CHECKER",
												"DEPARTMENT_MAKER",
											];
										}

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
																width: 32,
																height: 32,
										bgcolor: "var(--surface-hover)",
																fontSize: "12px",
																fontWeight: 600,
															}}
														>
															{initials}
														</Avatar>
														<Box>
															<Typography variant="body2" sx={{ fontWeight: 600, color: "var(--text-primary)" }}>
																{u.name}
																{isCurrent && (
																	<Chip
																		label="You"
																		size="small"
																		sx={{
																			ml: 1,
																			height: 18,
																			fontSize: "0.65rem",
															bgcolor: "var(--surface-secondary)",
															color: "var(--text-secondary)",
																		}}
																	/>
																)}
															</Typography>
															<Typography variant="caption" sx={{ color: "var(--text-secondary)" }}>
																ID: #{u.id}
															</Typography>
														</Box>
													</Box>
												</TableCell>

												<TableCell>
													<Typography variant="body2" sx={{ color: "var(--text-primary)" }}>{u.email}</Typography>
													<Typography variant="caption" sx={{ color: "var(--text-secondary)", display: "block" }}>
														{u.has_password ? "• Password set" : u.google_linked ? "• Google linked" : "• Setup pending"}
													</Typography>
												</TableCell>

												<TableCell>
													<Select
														size="small"
														value={roleVal}
														disabled={
															isCurrent ||
															updatingUserId === u.id ||
															(!isSuperAdmin && isTargetSuperAdmin)
														}
														onChange={(e) => handleRoleChange(u.id, e.target.value)}
														sx={{
															fontSize: "0.75rem",
															height: 30,
															fontWeight: 600,
															borderRadius: "8px",
															backgroundColor: "var(--surface)",
															color: "var(--text-primary)",
														}}
													>
														{allowedRoles.map((r) => (
															<MenuItem key={r} value={r} sx={{ fontSize: "0.8rem" }}>
																{roleLabel(r)}
															</MenuItem>
														))}
													</Select>
												</TableCell>

												<TableCell>
													{isSuperAdmin ? (
														<Select
															size="small"
															value={u.department?.toUpperCase() || (roleVal === "SUPER_ADMIN" ? "NONE" : "UNASSIGNED")}
															disabled={updatingUserId === u.id}
															onChange={(e) => {
																if (e.target.value !== "UNASSIGNED") {
																	handleDepartmentChange(u.id, e.target.value === "NONE" ? "" : e.target.value);
																}
															}}
															sx={{
																fontSize: "0.75rem",
																height: 30,
																fontWeight: 600,
																borderRadius: "8px",
																backgroundColor: "var(--surface)",
										color: !u.department && roleVal !== "SUPER_ADMIN" ? "var(--text-secondary)" : "var(--text-primary)",
															}}
														>
															{roleVal === "SUPER_ADMIN" ? (
																<MenuItem value="NONE">Cross-Dept / All</MenuItem>
															) : (
																!u.department && (
												<MenuItem value="UNASSIGNED" disabled sx={{ color: "var(--text-muted)", fontWeight: 500 }}>
																		⚠ Dept Unassigned
																	</MenuItem>
																)
															)}
															<MenuItem value="HR">HR</MenuItem>
															<MenuItem value="FINANCE">Finance</MenuItem>
															<MenuItem value="LEGAL">Legal</MenuItem>
															<MenuItem value="OPERATIONS">Operations</MenuItem>
															<MenuItem value="IT">IT</MenuItem>
														</Select>
													) : (
														<Chip
															label={u.department || "None"}
															size="small"
															sx={{
																height: 22,
																fontSize: "0.7rem",
											bgcolor: "var(--surface-secondary)",
											color: "var(--text-secondary)",
																fontWeight: 600,
															}}
														/>
													)}
												</TableCell>

												<TableCell>
													<Chip
														label={u.is_active ? "Active" : "Inactive"}
														size="small"
														color={u.is_active ? "success" : "default"}
														variant="outlined"
														sx={{ height: 22, fontSize: "0.7rem", fontWeight: 600 }}
													/>
												</TableCell>

												<TableCell>
													<Typography variant="caption" sx={{ color: "var(--text-secondary)" }}>
														{u.created_at ? new Date(u.created_at).toLocaleDateString() : "--"}
													</Typography>
												</TableCell>

												<TableCell>
													<Typography variant="caption" sx={{ color: "var(--text-secondary)" }}>
														{u.last_login ? new Date(u.last_login).toLocaleString() : "Never"}
													</Typography>
												</TableCell>

												<TableCell align="right">
													<Box sx={{ display: "flex", justifyContent: "flex-end", gap: 1 }}>
														<Button
															size="small"
															variant="text"
															disabled={isCurrent || updatingUserId === u.id || (!isSuperAdmin && isTargetSuperAdmin)}
															onClick={() => handleToggleActive(u)}
															sx={{
																fontSize: "0.7rem",
												color: u.is_active ? "var(--danger)" : "var(--success)",
																textTransform: "none",
																fontWeight: 600,
															}}
														>
															{u.is_active ? "Deactivate" : "Activate"}
														</Button>

														<IconButton
															size="small"
															disabled={isCurrent || isTargetSuperAdmin}
															onClick={() => {
																setDeleteTargetUser(u);
																setDeletePermanent(true);
															}}
															title="Permanently delete user"
															sx={{
											color: isCurrent || isTargetSuperAdmin ? "var(--text-muted)" : "var(--danger)",
											"&:hover": { backgroundColor: "var(--surface-hover)" },
															}}
														>
															<DeleteIcon fontSize="small" />
														</IconButton>
													</Box>
												</TableCell>
											</TableRow>
										);
									})
								)}
							</TableBody>
						</Table>
					</TableContainer>
				</>
			)}

			{/* ======================================================== */}
			{/* TAB 1: ACTIVITY LOGS */}
			{/* ======================================================== */}
			{currentTab === 1 && (
				<>
					{/* Activity Log Filter Bar */}
					<Paper
						sx={{
							p: 2,
							mb: 3,
							borderRadius: "14px",
							background: "var(--surface)",
							color: "var(--text-primary)",
							border: "1px solid var(--border)",
							display: "flex",
							flexWrap: "wrap",
							gap: 2,
							alignItems: "center",
						}}
					>
						<TextField
							size="small"
							placeholder="Search action, user, details..."
							value={logSearch}
							onChange={(e) => setLogSearch(e.target.value)}
							onKeyDown={(e) => {
								if (e.key === "Enter") fetchLogs(0, logPageSize);
							}}
							slotProps={{
								input: {
									startAdornment: <SearchIcon sx={{ color: "var(--text-secondary)", mr: 1, fontSize: 20 }} />,
								},
							}}
							sx={{ minWidth: 240, "& .MuiInputBase-input": { fontSize: "0.85rem", color: "var(--text-primary)" } }}
						/>

						<TextField
							size="small"
							type="date"
							label="From"
							value={logStartDate}
							onChange={(e) => setLogStartDate(e.target.value)}
							slotProps={{ inputLabel: { shrink: true } }}
							sx={{ width: 140, "& .MuiInputBase-input": { fontSize: "0.8rem", color: "var(--text-primary)" } }}
						/>

						<TextField
							size="small"
							type="date"
							label="To"
							value={logEndDate}
							onChange={(e) => setLogEndDate(e.target.value)}
							slotProps={{ inputLabel: { shrink: true } }}
							sx={{ width: 140, "& .MuiInputBase-input": { fontSize: "0.8rem", color: "var(--text-primary)" } }}
						/>

						<Select
							size="small"
							value={logAction}
							onChange={(e) => setLogAction(e.target.value)}
							sx={{ minWidth: 150, fontSize: "0.8rem", height: 38, color: "var(--text-primary)" }}
						>
							<MenuItem value="ALL">All Actions</MenuItem>
							<MenuItem value="LOGIN_SUCCESS">Login Success</MenuItem>
							<MenuItem value="LOGIN_FAILED">Login Failed</MenuItem>
							<MenuItem value="LOGOUT">Logout</MenuItem>
							<MenuItem value="DOCUMENT_UPLOAD">Document Upload</MenuItem>
							<MenuItem value="DOCUMENT_VIEW">Document View</MenuItem>
							<MenuItem value="DOCUMENT_VERIFIED">Document Verified</MenuItem>
							<MenuItem value="DOCUMENT_REVISION_REQUESTED">Revision Requested</MenuItem>
							<MenuItem value="DOCUMENT_DELETED">Document Deleted</MenuItem>
							<MenuItem value="OCR_TEXT_CORRECTED">OCR Text Corrected</MenuItem>
							<MenuItem value="USER_ROLE_CHANGED">Role Changed</MenuItem>
							<MenuItem value="USER_CREATE">User Created</MenuItem>
							<MenuItem value="USER_DEACTIVATE">User Deactivated</MenuItem>
							<MenuItem value="USER_DELETE">User Deleted</MenuItem>
							<MenuItem value="UNAUTHORIZED_ACCESS">Unauthorized Access</MenuItem>
						</Select>

						<Select
							size="small"
							value={logStatus}
							onChange={(e) => setLogStatus(e.target.value)}
							sx={{ minWidth: 130, fontSize: "0.8rem", height: 38, color: "var(--text-primary)" }}
						>
							<MenuItem value="ALL">All Status</MenuItem>
							<MenuItem value="SUCCESS">Success</MenuItem>
							<MenuItem value="FAILURE">Failure</MenuItem>
						</Select>

						{isSuperAdmin && (
							<Select
								size="small"
								value={logDept}
								onChange={(e) => setLogDept(e.target.value)}
								sx={{ minWidth: 150, fontSize: "0.8rem", height: 38, color: "var(--text-primary)" }}
							>
								<MenuItem value="ALL">All Departments</MenuItem>
								<MenuItem value="HR">HR</MenuItem>
								<MenuItem value="FINANCE">Finance</MenuItem>
								<MenuItem value="LEGAL">Legal</MenuItem>
								<MenuItem value="OPERATIONS">Operations</MenuItem>
								<MenuItem value="IT">IT</MenuItem>
							</Select>
						)}

						<Button
							variant="contained"
							onClick={() => fetchLogs(0, logPageSize)}
							disabled={logsLoading}
							sx={{
								bgcolor: "var(--accent)",
								"&:hover": { bgcolor: "var(--primary-hover)" },
								borderRadius: "8px",
								textTransform: "none",
								fontWeight: 600,
								height: 38,
							}}
						>
							Filter Logs
						</Button>
					</Paper>

					{/* Activity Log Table */}
					<TableContainer
						component={Paper}
						sx={{
							borderRadius: "14px",
							background: "var(--surface)",
							color: "var(--text-primary)",
							border: "1px solid var(--border)",
							overflowX: "auto",
						}}
					>
						<Table size="small" sx={{ minWidth: 1000, tableLayout: "fixed" }}>
							<TableHead sx={{ background: "var(--surface-secondary)" }}>
								<TableRow>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)", width: "165px" }}>Timestamp (UTC)</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)", width: "155px" }}>Actor</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)", width: "145px" }}>Role &amp; Dept</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)", width: "165px" }}>Action</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)", width: "130px" }}>Target</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)", width: "95px" }}>Status</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)", width: "230px" }}>Details</TableCell>
									<TableCell sx={{ fontWeight: 600, color: "var(--text-primary)", width: "115px" }}>IP</TableCell>
								</TableRow>
							</TableHead>
							<TableBody>
								{logsLoading ? (
									<TableRow>
										<TableCell colSpan={8} align="center" sx={{ py: 6 }}>
											<CircularProgress size={30} sx={{ color: "var(--accent)" }} />
										</TableCell>
									</TableRow>
								) : activityLogs.length === 0 ? (
									<TableRow>
										<TableCell colSpan={8} align="center" sx={{ py: 4, color: "var(--text-secondary)" }}>
											No activity logs found.
										</TableCell>
									</TableRow>
								) : (
									activityLogs.map((log) => {
										const isSuccess = log.status === "SUCCESS";
										const isFailure = log.status === "FAILURE" || log.action.includes("FAILED") || log.action.includes("UNAUTHORIZED");

										return (
											<TableRow
												key={log.id}
												hover
												sx={{
													"&:last-child td, &:last-child th": { border: 0 },
												}}
											>
												<TableCell sx={{ whiteSpace: "normal" }}>
													<Typography variant="caption" sx={{ fontFamily: "monospace", color: "var(--text-secondary)" }}>
														{new Date(log.timestamp).toLocaleString(undefined, {
															month: "short",
															day: "numeric",
															hour: "2-digit",
															minute: "2-digit",
															second: "2-digit",
														})}
													</Typography>
												</TableCell>

												<TableCell sx={{ overflow: "hidden", textOverflow: "ellipsis" }}>
													<Typography variant="body2" sx={{ fontWeight: 600, fontSize: "0.8rem", color: "var(--text-primary)" }}>
														{log.username || (log.actor_id ? `User #${log.actor_id}` : "System")}
													</Typography>
												</TableCell>

												<TableCell>
													<Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
														{log.department && (
															<Chip
																label={log.department}
																size="small"
																sx={{
																	height: 18,
																	fontSize: "0.65rem",
												bgcolor: "var(--surface-secondary)",
													color: "var(--text-secondary)",
																	fontWeight: 600,
																}}
															/>
														)}
														{log.role && (
															<Chip
																label={roleLabel(log.role)}
																size="small"
																sx={{
																	height: 18,
																	fontSize: "0.65rem",
												bgcolor: "var(--surface-secondary)",
													color: "var(--text-secondary)",
																	fontWeight: 600,
																}}
															/>
														)}
													</Box>
												</TableCell>

												<TableCell>
													<Typography
														variant="caption"
														sx={{
															fontWeight: 700,
															display: "inline-block",
															px: 0.8,
															py: 0.2,
															borderRadius: "4px",
									bgcolor: "var(--surface-secondary)",
									color: isFailure ? "var(--danger)" : "var(--info)",
														}}
													>
														{log.action}
													</Typography>
												</TableCell>

												<TableCell sx={{ overflow: "hidden", textOverflow: "ellipsis" }}>
													<Typography variant="caption" sx={{ color: "var(--text-secondary)" }}>
														{log.target_resource || "--"}
													</Typography>
												</TableCell>

												<TableCell>
													<Chip
														label={log.status}
														size="small"
														sx={{
															height: 20,
															fontSize: "0.65rem",
															fontWeight: 700,
									bgcolor: "var(--surface-secondary)",
									color: isSuccess ? "var(--success)" : "var(--danger)",
														}}
													/>
												</TableCell>

												<TableCell>
													<Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1 }}>
														<Typography
															variant="caption"
															sx={{
																color: "var(--text-primary)",
																display: "-webkit-box",
																WebkitLineClamp: 1,
																WebkitBoxOrient: "vertical",
																overflow: "hidden",
																fontSize: "0.75rem",
															}}
														>
															{log.failure_reason || log.details || "--"}
														</Typography>
														<Button
															size="small"
															variant="outlined"
															onClick={() => setSelectedLogDetail(log)}
															sx={{
																fontSize: "0.65rem",
																textTransform: "none",
																px: 1,
																py: 0.2,
																minWidth: "auto",
																borderRadius: "6px",
																borderColor: "var(--border)",
																color: "var(--text-secondary)",
															}}
														>
															Details
														</Button>
													</Box>
												</TableCell>

												<TableCell>
													<Typography variant="caption" sx={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>
														{log.ip_address || "--"}
													</Typography>
												</TableCell>
											</TableRow>
										);
									})
								)}
							</TableBody>
						</Table>
						<TablePagination
							component="div"
							count={logsTotal}
							page={logPage}
							onPageChange={(_, newPage) => {
								setLogPage(newPage);
								fetchLogs(newPage, logPageSize);
							}}
							rowsPerPage={logPageSize}
							onRowsPerPageChange={(e) => {
								const newSize = parseInt(e.target.value, 10);
								setLogPageSize(newSize);
								setLogPage(0);
								fetchLogs(0, newSize);
							}}
							rowsPerPageOptions={[10, 20, 50, 100]}
							sx={{ borderTop: "1px solid var(--border)", color: "var(--text-primary)" }}
						/>
					</TableContainer>
				</>
			)}

			{/* ======================================================== */}
			{/* LOG DETAILS MODAL */}
			{/* ======================================================== */}
			<Dialog
				open={Boolean(selectedLogDetail)}
				onClose={() => setSelectedLogDetail(null)}
				maxWidth="sm"
				fullWidth
				slotProps={{
					paper: {
						sx: {
							borderRadius: "14px",
							background: "var(--surface)",
							color: "var(--text-primary)",
							border: "1px solid var(--border)",
						},
					},
				}}
			>
				{selectedLogDetail && (
					<>
						<DialogTitle sx={{ fontWeight: 700, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
							<span>Audit Event: {selectedLogDetail.action}</span>
							<Chip
								label={selectedLogDetail.status}
								size="small"
								sx={{
					bgcolor: "var(--surface-secondary)",
					color: selectedLogDetail.status === "SUCCESS" ? "var(--success)" : "var(--danger)",
									fontWeight: 700,
								}}
							/>
						</DialogTitle>
						<DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
							<Box sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1, p: 1.5, bgcolor: "var(--surface-secondary)", borderRadius: "8px" }}>
								<Typography variant="caption"><strong>Timestamp:</strong> {new Date(selectedLogDetail.timestamp).toISOString()}</Typography>
								<Typography variant="caption"><strong>Actor:</strong> {selectedLogDetail.username || selectedLogDetail.actor_id || "System"}</Typography>
								<Typography variant="caption"><strong>Role:</strong> {selectedLogDetail.role || "--"}</Typography>
								<Typography variant="caption"><strong>Department:</strong> {selectedLogDetail.department || "--"}</Typography>
								<Typography variant="caption"><strong>Target Resource:</strong> {selectedLogDetail.target_resource || "--"}</Typography>
								<Typography variant="caption"><strong>IP Address:</strong> {selectedLogDetail.ip_address || "--"}</Typography>
							</Box>

							{selectedLogDetail.failure_reason && (
				<Box sx={{ p: 1.5, borderRadius: "8px", bgcolor: "var(--surface-secondary)", border: "1px solid var(--border)" }}>
					<Typography variant="caption" sx={{ color: "var(--danger)", fontWeight: 700, display: "block" }}>
										Failure Reason:
									</Typography>
					<Typography variant="body2" sx={{ color: "var(--danger)" }}>
										{selectedLogDetail.failure_reason}
									</Typography>
								</Box>
							)}

							<Box>
								<Typography variant="caption" sx={{ fontWeight: 700, color: "var(--text-secondary)", mb: 0.5, display: "block" }}>
									Event Details:
								</Typography>
								<Box
									component="pre"
									sx={{
										p: 1.5,
										borderRadius: "8px",
										background: "var(--surface-secondary)",
										color: "var(--text-primary)",
										fontSize: "0.8rem",
										overflowX: "auto",
										fontFamily: "monospace",
										maxHeight: "260px",
										margin: 0,
									}}
								>
									{(() => {
										if (!selectedLogDetail.details) return "No extra metadata.";
										try {
											const parsed = JSON.parse(selectedLogDetail.details);
											return JSON.stringify(parsed, null, 2);
										} catch {
											return selectedLogDetail.details;
										}
									})()}
								</Box>
							</Box>
						</DialogContent>
						<DialogActions sx={{ p: 2 }}>
							<Button onClick={() => setSelectedLogDetail(null)} sx={{ color: "var(--text-secondary)" }}>
								Close
							</Button>
						</DialogActions>
					</>
				)}
			</Dialog>

			{/* ======================================================== */}
			{/* INVITE USER DIALOG */}
			{/* ======================================================== */}
			<Dialog
				open={inviteOpen}
				onClose={() => setInviteOpen(false)}
				slotProps={{
					paper: {
						sx: {
							borderRadius: "14px",
							background: "var(--surface)",
							color: "var(--text-primary)",
							border: "1px solid var(--border)",
							minWidth: 400,
						},
					},
				}}
			>
				<form onSubmit={handleInviteSubmit}>
					<DialogTitle sx={{ fontWeight: 700 }}>Add / Invite User</DialogTitle>
					<DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2, pt: 1 }}>
						<TextField
							label="Full Name"
							required
							size="small"
							value={inviteForm.name}
							onChange={(e) => setInviteForm({ ...inviteForm, name: e.target.value })}
							fullWidth
						/>
						<TextField
							label="Email Address"
							type="email"
							required
							size="small"
							value={inviteForm.email}
							onChange={(e) => setInviteForm({ ...inviteForm, email: e.target.value })}
							fullWidth
						/>
						<TextField
							label="Password (for direct login)"
							type="password"
							size="small"
							value={inviteForm.password}
							onChange={(e) => setInviteForm({ ...inviteForm, password: e.target.value })}
							helperText="Optional: leave blank for Google OAuth login only"
							fullWidth
						/>
						<Select
							size="small"
							value={inviteForm.role}
							onChange={(e) => setInviteForm({ ...inviteForm, role: e.target.value })}
							fullWidth
						>
							{isSuperAdmin && <MenuItem value="SUPER_ADMIN">Super Admin</MenuItem>}
							{isSuperAdmin && <MenuItem value="DEPARTMENT_ADMIN">Department Admin</MenuItem>}
							<MenuItem value="DEPARTMENT_CHECKER">Department Checker</MenuItem>
							<MenuItem value="DEPARTMENT_MAKER">Department Maker</MenuItem>
						</Select>

						{isSuperAdmin ? (
							<Box>
								<Typography variant="caption" sx={{ color: "var(--text-secondary)", display: "block", mb: 0.5, fontWeight: 600 }}>
									Department {inviteForm.role !== "SUPER_ADMIN" ? "(Required)" : "(Optional for Super Admin)"}
								</Typography>
								<Select
									size="small"
									value={inviteForm.department || (inviteForm.role === "SUPER_ADMIN" ? "NONE" : "")}
									onChange={(e) =>
										setInviteForm({
											...inviteForm,
											department: e.target.value === "NONE" || e.target.value === "" ? null : e.target.value,
										})
									}
									fullWidth
									displayEmpty
								>
									{inviteForm.role === "SUPER_ADMIN" ? (
										<MenuItem value="NONE">Cross-Department / All</MenuItem>
									) : (
										<MenuItem value="" disabled sx={{ color: "var(--text-muted)" }}>
											Select Department (Required)
										</MenuItem>
									)}
									<MenuItem value="HR">HR</MenuItem>
									<MenuItem value="FINANCE">Finance</MenuItem>
									<MenuItem value="LEGAL">Legal</MenuItem>
									<MenuItem value="OPERATIONS">Operations</MenuItem>
									<MenuItem value="IT">IT</MenuItem>
								</Select>
							</Box>
						) : (
							<Typography variant="caption" sx={{ color: "var(--text-secondary)" }}>
								Department locked to: <strong>{currentUser?.department}</strong>
							</Typography>
						)}
					</DialogContent>
					<DialogActions sx={{ p: 2 }}>
						<Button type="button" onClick={() => setInviteOpen(false)} sx={{ color: "var(--text-secondary)" }}>
							Cancel
						</Button>
						<Button
							type="submit"
							variant="contained"
							disabled={inviteSubmitting}
							sx={{
								bgcolor: "var(--accent)",
								"&:hover": { bgcolor: "var(--primary-hover)" },
							}}
						>
							{inviteSubmitting ? "Adding..." : "Add User"}
						</Button>
					</DialogActions>
				</form>
			</Dialog>

			{/* ======================================================== */}
			{/* DELETE USER CONFIRMATION DIALOG */}
			{/* ======================================================== */}
			<Dialog
				open={Boolean(deleteTargetUser)}
				onClose={() => setDeleteTargetUser(null)}
				slotProps={{
					paper: {
						sx: {
							borderRadius: "14px",
							background: "var(--surface)",
							color: "var(--text-primary)",
							border: "1px solid var(--border)",
						},
					},
				}}
			>
				<DialogTitle sx={{ fontWeight: 700, color: "var(--danger)" }}>
					Confirm User Deletion
				</DialogTitle>
				<DialogContent>
					<DialogContentText sx={{ color: "var(--text-primary)", mb: 2 }}>
						Are you sure you want to permanently delete{" "}
						<strong>{deleteTargetUser?.name} ({deleteTargetUser?.email})</strong>?
					</DialogContentText>
					<Typography variant="body2" sx={{ color: "var(--text-secondary)" }}>
						• The user will be completely removed from the user list.
						<br />
						• All previously uploaded documents will be safely preserved with document attribution intact.
						<br />
						• Historical audit logs will remain preserved.
					</Typography>
				</DialogContent>
				<DialogActions sx={{ p: 2 }}>
					<Button onClick={() => setDeleteTargetUser(null)} sx={{ color: "var(--text-secondary)" }}>
						Cancel
					</Button>
					<Button
						variant="contained"
						color="error"
						disabled={deleting}
						onClick={handleConfirmDelete}
					>
						{deleting ? "Deleting..." : "Permanently Delete"}
					</Button>
				</DialogActions>
			</Dialog>

			{/* Feedback Snackbar */}
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
