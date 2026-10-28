import SearchIcon from "@mui/icons-material/Search";
import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import LogoutIcon from "@mui/icons-material/Logout";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";

export default function TopBar() {
  const [open, setOpen] = useState(false);
  const { user, isAdmin, logout: authLogout } = useAuth();
  const [searchQuery, setSearchQuery] = useState("");
  const [failedProfilePicture, setFailedProfilePicture] = useState<string | null>(null);

  const dropdownRef = useRef<HTMLDivElement>(null);
  const profileButtonRef = useRef<HTMLButtonElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        profileButtonRef.current?.focus();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open]);

  const displayName = user?.name || "User";
  const normalizedRole = user?.role.toUpperCase();
  const displayRole = normalizedRole === "ADMIN"
    ? "Admin"
    : normalizedRole === "UPLOAD_CHECKER"
      ? "Upload Checker"
      : normalizedRole === "UPLOAD_MAKER"
        ? "Upload Maker"
        : "User";
  const profilePicture = user?.profile_picture && user.profile_picture !== failedProfilePicture
    ? user.profile_picture
    : undefined;

  const initials = displayName
    .split(" ")
    .map((word: string) => word[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const handleLogout = () => {
    setOpen(false);
    authLogout();
    navigate("/login");
  };

  const handleSearch = () => {
    const trimmedQuery = searchQuery.trim();

    if (!trimmedQuery) {
      navigate("/ask");
      return;
    }

    navigate(`/ask?q=${encodeURIComponent(trimmedQuery)}`);
    setSearchQuery("");
  };

  return (
    <header className="topbar">
      <form
        className="searchBox"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          handleSearch();
        }}
      >
        <button className="searchSubmit" type="submit" aria-label="Search documents">
          <SearchIcon />
        </button>

        <input
          type="text"
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          placeholder="Ask about any document..."
          aria-label="Ask about any document"
        />
      </form>

      <div className="topbarRight">
        <div className="profileWrapper" ref={dropdownRef}>
          <button
            type="button"
            className="profile"
          ref={profileButtonRef}
            onClick={() => setOpen((prev) => !prev)}
          aria-expanded={open}
          aria-haspopup="menu"
          aria-controls={open ? "profile-menu" : undefined}
          >
            {profilePicture ? (
              <img
                src={profilePicture}
                alt={displayName}
                onError={() => setFailedProfilePicture(profilePicture)}
                style={{
                  width: 34,
                  height: 34,
                  borderRadius: "50%",
                  objectFit: "cover",
                }}
              />
            ) : (
              <div className="avatar">{initials}</div>
            )}

            <span>{displayName.split(" ")[0]}</span>

            <KeyboardArrowDownIcon
              className={open ? "arrowUp" : ""}
            />
          </button>

          {open && (
            <div className="profileDropdown" id="profile-menu" role="menu" aria-label="Account menu">
              <div className="profileDropdownHeader">
                {profilePicture ? (
                  <img
                    src={profilePicture}
                    alt={displayName}
                    onError={() => setFailedProfilePicture(profilePicture)}
                    style={{
                      width: 40,
                      height: 40,
                      borderRadius: "50%",
                      objectFit: "cover",
                    }}
                  />
                ) : (
                  <div className="dropdownAvatar">{initials}</div>
                )}

                <div>
                  <strong>{displayName}</strong>
                  <span>{displayRole}</span>
                </div>
              </div>

              <div className="dropdownDivider" />

              <button
                type="button"
                className="dropdownItem"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  navigate("/profile");
                }}
              >
                <AccountCircleIcon />
                <span>Profile</span>
              </button>

              {isAdmin && (
                <button
                  type="button"
                  className="dropdownItem"
                  role="menuitem"
                  onClick={() => {
                    setOpen(false);
                    navigate("/admin");
                  }}
                >
                  <AdminPanelSettingsIcon />
                  <span>Admin Panel</span>
                </button>
              )}

              <button
                type="button"
                className="dropdownItem logoutItem"
                role="menuitem"
                onClick={handleLogout}
              >
                <LogoutIcon />
                <span>Log out</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
