import apiClient from "./client";

export interface User {
	id: number;
	email: string;
	name: string;
	role: "ADMIN" | "UPLOAD_MAKER" | "UPLOAD_CHECKER" | "admin" | "user" | string;
	department?: string | null;
	profile_picture?: string | null;
	is_active: boolean;
	created_at?: string;
	last_login?: string | null;
}


export interface TokenResponse {
	access_token: string;
	token_type: string;
	user: User;
}

export interface GoogleAuthUrlResponse {
	url: string;
}

export interface AdminLoginPayload {
	email: string;
	password: string;
}

/**
 * Fetch Google OAuth authorization URL from backend
 */
export async function getGoogleAuthUrl(): Promise<string> {
	const response = await apiClient.get<GoogleAuthUrlResponse>("/auth/google");
	return response.data.url;
}

/**
 * Log in using admin credentials
 */
export async function adminLogin(payload: AdminLoginPayload): Promise<TokenResponse> {
	const response = await apiClient.post<TokenResponse>("/auth/admin/login", payload);
	return response.data;
}

/**
 * General credentials login (admin or regular user with password)
 */
export async function credentialsLogin(payload: AdminLoginPayload): Promise<TokenResponse> {
	const response = await apiClient.post<TokenResponse>("/auth/login", payload);
	return response.data;
}

/**
 * Get profile of currently authenticated user
 */
export async function getCurrentUser(): Promise<User> {
	const response = await apiClient.get<User>("/auth/me");
	return response.data;
}

/**
 * Log out user from backend session
 */
export async function logoutUser(): Promise<{ message: string }> {
	const response = await apiClient.post<{ message: string }>("/auth/logout");
	return response.data;
}

/**
 * Admin only: List all registered users
 */
export async function getAdminUsers(): Promise<User[]> {
	const response = await apiClient.get<User[]>("/auth/admin/users");
	return response.data;
}

export interface UserUpdatePayload {
	role?: string;
	department?: string | null;
	is_active?: boolean;
	name?: string;
}

/**
 * Admin only: Update user role, department, or active status
 */
export async function updateAdminUser(
	userId: number,
	payload: UserUpdatePayload
): Promise<User> {
	const response = await apiClient.patch<User>(`/auth/admin/users/${userId}`, payload);
	return response.data;
}

/**
 * Admin only: Update user role specifically
 */
export async function updateUserRole(
	userId: number,
	role: string
): Promise<User> {
	const response = await apiClient.patch<User>(`/admin/users/${userId}/role`, { role });
	return response.data;
}

export interface UserInvitePayload {
	name: string;
	email: string;
	role: string;
	department?: string | null;
	password?: string;
}

/**
 * Admin only: Invite a new user
 */
export async function inviteAdminUser(payload: UserInvitePayload): Promise<User> {
	const response = await apiClient.post<User>("/admin/users/invite", payload);
	return response.data;
}

/**
 * Admin only: Deactivate/delete a user
 */
export async function deleteAdminUser(userId: number): Promise<{ message: string }> {
	const response = await apiClient.delete<{ message: string }>(`/admin/users/${userId}`);
	return response.data;
}

export interface FolderPermissionItem {
	folder: string;
	admin: boolean;
	upload_maker: boolean;
	upload_checker: boolean;
}

/**
 * Admin only: Get folder access permissions matrix
 */
export async function getPermissionsMatrix(): Promise<FolderPermissionItem[]> {
	const response = await apiClient.get<FolderPermissionItem[]>("/admin/permissions");
	return response.data;
}

/**
 * Admin only: Save permissions matrix
 */
export async function updatePermissionsMatrix(
	permissions: FolderPermissionItem[]
): Promise<FolderPermissionItem[]> {
	const response = await apiClient.put<FolderPermissionItem[]>("/admin/permissions", permissions);
	return response.data;
}

export interface WorkflowRule {
	id?: number;
	type: "warning" | "security" | "claims" | string;
	title: string;
	route: string;
	document_type?: string | null;
	threshold?: number;
}

export interface WorkflowConfig {
	threshold: number;
	rules: WorkflowRule[];
}

/**
 * Admin only: Get workflow settings and routing rules
 */
export async function getWorkflowConfig(): Promise<WorkflowConfig> {
	const response = await apiClient.get<WorkflowConfig>("/admin/workflow");
	return response.data;
}

/**
 * Admin only: Update workflow configuration
 */
export async function updateWorkflowConfig(
	payload: { threshold: number; rules?: WorkflowRule[] }
): Promise<WorkflowConfig> {
	const response = await apiClient.put<WorkflowConfig>("/admin/workflow", payload);
	return response.data;
}

/**
 * Admin only: Add a new routing rule
 */
export async function addWorkflowRule(
	payload: { title: string; route: string; type?: string; threshold?: number; document_type?: string | null }
): Promise<WorkflowRule> {
	const response = await apiClient.post<WorkflowRule>("/admin/workflow/rules", payload);
	return response.data;
}

/**
 * Admin only: Delete a routing rule
 */
export async function deleteWorkflowRule(ruleId: number): Promise<{ message: string }> {
	const response = await apiClient.delete<{ message: string }>(`/admin/workflow/rules/${ruleId}`);
	return response.data;
}


