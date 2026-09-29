import NotificationsNoneIcon from "@mui/icons-material/NotificationsNone";
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

  const dropdownRef = useRef<HTMLDivElement>(null);
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

  const displayName = user?.name || "User";
  const displayRole = user?.role
    ? user.role.charAt(0).toUpperCase() + user.role.slice(1)
    : "User";
  const profilePicture = user?.profile_picture;

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
      <div className="searchBox">
        <SearchIcon />

        <input
          type="text"
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              handleSearch();
            }
          }}
          placeholder="Ask about any document..."
          aria-label="Ask about any document"
        />
      </div>

      <div className="topbarRight">
        <button
          type="button"
          className="notificationButton"
          aria-label="Notifications"
        >
          <NotificationsNoneIcon />

          <span className="notificationDot" />
        </button>

        <div className="profileWrapper" ref={dropdownRef}>
          <button
            type="button"
            className="profile"
            onClick={() => setOpen((prev) => !prev)}
          >
            {profilePicture ? (
              <img
                src={profilePicture}
                alt={displayName}
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
            <div className="profileDropdown">
              <div className="profileDropdownHeader">
                {profilePicture ? (
                  <img
                    src={profilePicture}
                    alt={displayName}
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