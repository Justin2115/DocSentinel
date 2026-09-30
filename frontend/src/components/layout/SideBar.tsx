import {
  Dashboard,
  Upload,
  Folder,
  Chat,
  AssignmentTurnedIn,
  Settings,
  LightMode,
  DarkMode,
  ChevronLeft,
  AdminPanelSettings,
} from "@mui/icons-material";
import { NavLink } from "react-router-dom";
import { useEffect, useState } from "react";
import { useAuth } from "../../context/AuthContext";

export default function SideBar() {
  const { isAdmin, canUpload, canReview } = useAuth();
  const [darkMode, setDarkMode] = useState(
    () => localStorage.getItem("theme") === "dark"
  );

  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
    localStorage.setItem("theme", darkMode ? "dark" : "light");
  }, [darkMode]);

  const navigationItems = [
    { name: "Dashboard", path: "/dashboard", icon: <Dashboard /> },
    ...(canUpload
      ? [{ name: "Upload", path: "/upload", icon: <Upload /> }]
      : []),
    { name: "Library", path: "/library", icon: <Folder /> },
    { name: "Ask a question", path: "/ask", icon: <Chat /> },
    ...(canReview
      ? [{ name: "Review queue", path: "/review", icon: <AssignmentTurnedIn /> }]
      : []),
    ...(isAdmin
      ? [{ name: "Settings", path: "/settings", icon: <Settings /> }]
      : []),
    ...(isAdmin
      ? [{ name: "Admin Panel", path: "/admin", icon: <AdminPanelSettings /> }]
      : []),
  ];

  return (
    <aside className={collapsed ? "sidebar collapsed" : "sidebar"}>
      <div>
        <div className="brand">
          <div className="brandLogo">D</div>
          {!collapsed && (
            <span className="brandText">DocSentinel</span>
          )}
        </div>

        <nav>
          {navigationItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) =>
                isActive ? "navItem active" : "navItem"
              }
            >
              {item.icon}
              {!collapsed && <span>{item.name}</span>}
            </NavLink>
          ))}
        </nav>
      </div>

      <div className="sidebarBottom">
        <button
          type="button"
          className="modeButton"
          onClick={() => setDarkMode((prev) => !prev)}
          title={darkMode ? "Light mode" : "Dark mode"}
        >
          {darkMode ? <LightMode /> : <DarkMode />}
          {!collapsed && (
            <span>{darkMode ? "Light mode" : "Dark mode"}</span>
          )}
        </button>

        <button
          type="button"
          className="collapseButton"
          onClick={() => setCollapsed((prev) => !prev)}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <ChevronLeft
            className={collapsed ? "rotateIcon" : ""}
          />
          {!collapsed && <span>Collapse</span>}
        </button>
      </div>
    </aside>
  );
}