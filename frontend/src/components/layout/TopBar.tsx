import NotificationsNoneIcon from "@mui/icons-material/NotificationsNone";
import SearchIcon from "@mui/icons-material/Search";
import AccountCircleIcon from "@mui/icons-material/AccountCircle";import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import LogoutIcon from "@mui/icons-material/Logout";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

export default function TopBar() {
  const [open, setOpen] = useState(false);
  const [profile, setProfile] = useState(() => {
    const saved = localStorage.getItem("profile");

    return saved
      ? JSON.parse(saved)
      : {
          name: "Elvina Binoy",
          email: "elvina@docsentinel.io",
          phone: "",
          role: "Admin",
        };
  });

  const dropdownRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const updateProfile = () => {
      const saved = localStorage.getItem("profile");

      if (saved) {
        setProfile(JSON.parse(saved));
      }
    };

    window.addEventListener("profileUpdated", updateProfile);

    return () => {
      window.removeEventListener("profileUpdated", updateProfile);
    };
  }, []);

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

  const initials = profile.name
    .split(" ")
    .map((word: string) => word[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const logout = () => {
    localStorage.removeItem("auth");
    navigate("/login");
  };

  return (
    <header className="topbar">
      <div className="searchBox">
        <SearchIcon />

        <input placeholder="Ask about any document..." />
      </div>

      <div className="topbarRight">
        <button className="notificationButton">
          <NotificationsNoneIcon />

          <span className="notificationDot" />
        </button>

        <div className="profileWrapper" ref={dropdownRef}>
          <button
            className="profile"
            onClick={() => setOpen((prev) => !prev)}
          >
            <div className="avatar">{initials}</div>

            <span>{profile.name.split(" ")[0]}</span>

            <KeyboardArrowDownIcon
              className={open ? "arrowUp" : ""}
            />
          </button>

          {open && (
            <div className="profileDropdown">
              <div className="profileDropdownHeader">
                <div className="dropdownAvatar">{initials}</div>

                <div>
                  <strong>{profile.name}</strong>
                  <span>{profile.role}</span>
                </div>
              </div>

              <div className="dropdownDivider" />

              <button
                className="dropdownItem"
                onClick={() => {
                  setOpen(false);
                  navigate("/profile");
                }}
              >
                <AccountCircleIcon />
                <span>Profile</span>
              </button>

              <button
                className="dropdownItem logoutItem"
                onClick={logout}
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