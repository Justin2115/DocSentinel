import NotificationsNoneIcon from "@mui/icons-material/NotificationsNone";
import SearchIcon from "@mui/icons-material/Search";
import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
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

  const [searchQuery, setSearchQuery] = useState("");

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
          onFocus={() => {
            // If the user clicks the search box without typing,
            // they can still press Enter to open Ask a Question.
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

              <button
                type="button"
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