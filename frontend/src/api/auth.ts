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
	const response = await apiClient.patch<User>(`/auth/admin/users/${userId}/role`, { role });
	return response.data;
}

