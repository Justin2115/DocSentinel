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
import { NavLink, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import { useAuth } from "../../context/AuthContext";

export default function SideBar() {
  const { isAdmin, canUpload, canReview } = useAuth();
  const [darkMode, setDarkMode] = useState(
    () => localStorage.getItem("theme") === "dark"
  );

  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
    document.body.classList.toggle("dark-mode", darkMode);
    document.documentElement.setAttribute("data-theme", darkMode ? "dark" : "light");
    localStorage.setItem("theme", darkMode ? "dark" : "light");
  }, [darkMode]);

  const groups = [
    {
      name: "Workspace",
      items: [
        { name: "Overview", path: "/dashboard", icon: <Dashboard /> },
        { name: "Documents", path: "/library", icon: <Folder /> },
        ...(canUpload ? [{ name: "Upload documents", path: "/upload", icon: <Upload /> }] : []),
        { name: "Ask DocSentinel", path: "/ask", icon: <Chat /> },
      ],
    },
    ...(canReview ? [{ name: "Processing", items: [{ name: "Review queue", path: "/review", icon: <AssignmentTurnedIn /> }] }] : []),
    ...(isAdmin ? [{ name: "Administration", items: [
      { name: "System settings", path: "/settings", icon: <Settings /> },
      { name: "Admin panel", path: "/admin", icon: <AdminPanelSettings /> },
    ] }] : []),
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

        <nav aria-label="Main navigation">
          {groups.map((group) => (
            <div className="navGroup" key={group.name}>
              {!collapsed && <div className="navGroupLabel">{group.name}</div>}
              {group.items.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              title={collapsed ? item.name : undefined}
              aria-current={location.pathname === item.path ? "page" : undefined}
              className={({ isActive }) =>
                isActive ? "navItem active" : "navItem"
              }
            >
              {item.icon}
              {!collapsed && <span>{item.name}</span>}
            </NavLink>
          ))}
            </div>
          ))}
        </nav>
      </div>

      <div className="sidebarBottom">
        <button
          type="button"
          className="modeButton"
          onClick={() => setDarkMode((prev) => !prev)}
          title={darkMode ? "Light mode" : "Dark mode"}
          aria-label={darkMode ? "Switch to light mode" : "Switch to dark mode"}
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
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
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
