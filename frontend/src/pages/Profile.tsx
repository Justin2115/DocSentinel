import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import EmailOutlinedIcon from "@mui/icons-material/EmailOutlined";
import ShieldOutlinedIcon from "@mui/icons-material/ShieldOutlined";
import { useAuth } from "../context/AuthContext";

const roleLabels: Record<string, string> = {
	ADMIN: "Admin",
	SUPER_ADMIN: "Super Admin",
	DEPARTMENT_ADMIN: "Department Admin",
	DEPARTMENT_CHECKER: "Department Checker",
	DEPARTMENT_MAKER: "Department Maker",
	UPLOAD_CHECKER: "Upload Checker",
	UPLOAD_MAKER: "Upload Maker",
};

export default function Profile() {
	const { user } = useAuth();
	const name = user?.name?.trim() || "User";
	const email = user?.email || "";
	const role = user?.role ? roleLabels[user.role.toUpperCase()] || user.role : "User";
	const initials = name
		.split(/\s+/)
		.map((word) => word[0])
		.join("")
		.slice(0, 2)
		.toUpperCase();

	return (
		<div className="profilePage">
			<div className="pageHeader">
				<div>
					<h1>Profile</h1>
					<p>View your account details and organization role.</p>
				</div>
			</div>

			<div className="profileGrid">
				<section className="profileCard profileOverview">
					{user?.profile_picture ? (
						<img
							src={user.profile_picture}
							alt={name}
							style={{
								width: 80,
								height: 80,
								borderRadius: "50%",
								objectFit: "cover",
								margin: "0 auto 16px",
							}}
						/>
					) : (
						<div className="largeAvatar">{initials}</div>
					)}
					<h2>{name}</h2>
					<p>{email}</p>
					<span className="roleBadge">{role}</span>
				</section>

				<section className="profileCard">
					<h2>Account information</h2>
					<div className="profileFields">
						<div className="profileField">
							<label><AccountCircleIcon /> Full name</label>
							<span>{name}</span>
						</div>
						<div className="profileField">
							<label><EmailOutlinedIcon /> Email address</label>
							<span>{email || "Not provided"}</span>
						</div>
						<div className="profileField">
							<label><ShieldOutlinedIcon /> Role</label>
							<span>{role}</span>
						</div>
					</div>
					<p role="note">To change your account details or role, contact your administrator.</p>
				</section>
			</div>
		</div>
	);
}
