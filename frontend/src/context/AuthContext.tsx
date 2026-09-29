import React, {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useState,
} from "react";
import { getCurrentUser, logoutUser } from "../api/auth";
import type { User } from "../api/auth";

interface AuthContextType {
	user: User | null;
	token: string | null;
	isAuthenticated: boolean;
	isAdmin: boolean;
	isLoading: boolean;
	login: (token: string, userData?: User) => Promise<void>;
	logout: () => void;
	refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
	const [token, setToken] = useState<string | null>(() => localStorage.getItem("auth_token"));
	const [user, setUser] = useState<User | null>(null);
	const [isLoading, setIsLoading] = useState<boolean>(true);

	const refreshUser = useCallback(async () => {
		const savedToken = localStorage.getItem("auth_token");
		if (!savedToken) {
			setUser(null);
			setIsLoading(false);
			return;
		}

		try {
			const profile = await getCurrentUser();
			setUser(profile);
			// Also sync with legacy profile localStorage so existing profile listeners still work
			localStorage.setItem("profile", JSON.stringify({
				name: profile.name,
				email: profile.email,
				role: profile.role.charAt(0).toUpperCase() + profile.role.slice(1),
				profile_picture: profile.profile_picture,
			}));
			window.dispatchEvent(new Event("profileUpdated"));
		} catch (error) {
			console.error("Failed to load authenticated user profile:", error);
			localStorage.removeItem("auth_token");
			setToken(null);
			setUser(null);
		} finally {
			setIsLoading(false);
		}
	}, []);

	useEffect(() => {
		refreshUser();
	}, [refreshUser]);

	const login = useCallback(async (newToken: string, userData?: User) => {
		localStorage.setItem("auth_token", newToken);
		setToken(newToken);
		if (userData) {
			setUser(userData);
			localStorage.setItem("profile", JSON.stringify({
				name: userData.name,
				email: userData.email,
				role: userData.role.charAt(0).toUpperCase() + userData.role.slice(1),
				profile_picture: userData.profile_picture,
			}));
			window.dispatchEvent(new Event("profileUpdated"));
			setIsLoading(false);
		} else {
			setIsLoading(true);
			await refreshUser();
		}
	}, [refreshUser]);

	const logout = useCallback(() => {
		try {
			logoutUser().catch(() => {});
		} catch (_) {}
		localStorage.removeItem("auth_token");
		localStorage.removeItem("profile");
		setToken(null);
		setUser(null);
	}, []);

	const isAuthenticated = Boolean(token && user);
	const isAdmin = Boolean(user && user.role === "admin");

	const contextValue = useMemo(
		() => ({
			user,
			token,
			isAuthenticated,
			isAdmin,
			isLoading,
			login,
			logout,
			refreshUser,
		}),
		[user, token, isAuthenticated, isAdmin, isLoading, login, logout, refreshUser]
	);

	return <AuthContext.Provider value={contextValue}>{children}</AuthContext.Provider>;
};

export const useAuth = (): AuthContextType => {
	const context = useContext(AuthContext);
	if (!context) {
		throw new Error("useAuth must be used within an AuthProvider");
	}
	return context;
};
