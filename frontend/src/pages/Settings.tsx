import { useState } from "react";

import GroupIcon from "@mui/icons-material/Group";
import ShieldOutlinedIcon from "@mui/icons-material/ShieldOutlined";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import DeleteIcon from "@mui/icons-material/Delete";
import AddIcon from "@mui/icons-material/Add";
import CheckIcon from "@mui/icons-material/Check";
import CloseIcon from "@mui/icons-material/Close";
import WarningAmberOutlinedIcon from "@mui/icons-material/WarningAmberOutlined";
import SecurityOutlinedIcon from "@mui/icons-material/SecurityOutlined";
import AssignmentOutlinedIcon from "@mui/icons-material/AssignmentOutlined";

type Tab = "users" | "permissions" | "workflow";

type Member = {
  name: string;
  email: string;
  initials: string;
  role: "Admin" | "Editor" | "Viewer";
};

const members: Member[] = [
  {
    name: "Elvina Binoy",
    email: "elvina@docsentinel.io",
    initials: "EB",
    role: "Admin",
  },
  {
    name: "Lucas Petit",
    email: "lucas@docsentinel.io",
    initials: "LP",
    role: "Editor",
  },
  {
    name: "Priya Nair",
    email: "priya@docsentinel.io",
    initials: "PN",
    role: "Viewer",
  },
  {
    name: "Marco Ricci",
    email: "marco@docsentinel.io",
    initials: "MR",
    role: "Editor",
  },
];

const permissions = [
  {
    folder: "Invoices",
    admin: true,
    editor: true,
    viewer: false,
  },
  {
    folder: "ID Verification",
    admin: true,
    editor: false,
    viewer: false,
  },
  {
    folder: "Contracts",
    admin: true,
    editor: true,
    viewer: true,
  },
  {
    folder: "Medical Claims",
    admin: true,
    editor: false,
    viewer: false,
  },
];

const routingRules = [
  {
    type: "warning",
    title: "Invoices below 80% confidence",
    route: "Finance team",
  },
  {
    type: "security",
    title: "ID documents below 70% confidence",
    route: "Compliance team",
  },
  {
    type: "claims",
    title: "Medical claims below 85% confidence",
    route: "Claims review team",
  },
];

export default function Settings() {
  const [activeTab, setActiveTab] = useState<Tab>("users");
  const [threshold, setThreshold] = useState(80);

  return (
    <div className="settingsPage">

      {/* HEADER */}

      <div className="settingsHeader">
        <h1>Settings</h1>

        <p>
          Manage your team, permissions, and automation rules
        </p>
      </div>

      {/* TABS */}

      <div className="settingsTabs">

        <button
          className={activeTab === "users" ? "settingsTab active" : "settingsTab"}
          onClick={() => setActiveTab("users")}
        >
          <GroupIcon />
          Users & roles
        </button>

        <button
          className={
            activeTab === "permissions"
              ? "settingsTab active"
              : "settingsTab"
          }
          onClick={() => setActiveTab("permissions")}
        >
          <ShieldOutlinedIcon />
          Permissions
        </button>

        <button
          className={
            activeTab === "workflow"
              ? "settingsTab active"
              : "settingsTab"
          }
          onClick={() => setActiveTab("workflow")}
        >
          <AccountTreeOutlinedIcon />
          Workflow rules
        </button>

      </div>

      {/* USERS & ROLES */}

      {activeTab === "users" && (
        <div className="settingsSection">

          <div className="sectionTop">
            <h2>Team members</h2>

            <button className="inviteButton">
              <AddIcon />
              Invite member
            </button>
          </div>

          <div className="settingsTable">

            <div className="settingsTableHeader">
              <span>Member</span>
              <span>Role</span>
              <span></span>
            </div>

            {members.map((member) => (
              <div
                className="memberRow"
                key={member.email}
              >

                <div className="memberInfo">

                  <div className="memberAvatar">
                    {member.initials}
                  </div>

                  <div>
                    <strong>{member.name}</strong>
                    <span>{member.email}</span>
                  </div>

                </div>

                <select
                  className="roleSelect"
                  defaultValue={member.role}
                >
                  <option>Admin</option>
                  <option>Editor</option>
                  <option>Viewer</option>
                </select>

                <button className="deleteMember">
                  <DeleteIcon />
                </button>

              </div>
            ))}

          </div>

        </div>
      )}

      {/* PERMISSIONS */}

      {activeTab === "permissions" && (
        <div className="settingsSection">

          <h2 className="sectionTitle">
            Folder access matrix
          </h2>

          <div className="permissionTable">

            <div className="permissionHeader">
              <span>Folder</span>
              <span>Admin</span>
              <span>Editor</span>
              <span>Viewer</span>
            </div>

            {permissions.map((item) => (
              <div
                className="permissionRow"
                key={item.folder}
              >

                <span className="folderName">
                  {item.folder}
                </span>

                <PermissionIcon allowed={item.admin} />

                <PermissionIcon allowed={item.editor} />

                <PermissionIcon allowed={item.viewer} />

              </div>
            ))}

          </div>

        </div>
      )}

      {/* WORKFLOW RULES */}

      {activeTab === "workflow" && (
        <div className="settingsSection">

          <h2 className="sectionTitle">
            Default confidence threshold
          </h2>

          <div className="thresholdCard">

            <div className="thresholdTop">

              <div>
                <h3>Auto-flag threshold</h3>

                <p>
                  Documents below this confidence are sent to
                  the review queue
                </p>
              </div>

              <strong>{threshold}%</strong>

            </div>

            <input
              type="range"
              min="50"
              max="100"
              value={threshold}
              onChange={(event) =>
                setThreshold(Number(event.target.value))
              }
              className="confidenceSlider"
            />

            <div className="sliderLabels">
              <span>50%</span>
              <span>75%</span>
              <span>100%</span>
            </div>

          </div>

          <h2 className="sectionTitle routingTitle">
            Routing rules
          </h2>

          <div className="routingRules">

            {routingRules.map((rule) => (

              <div
                className="routingRule"
                key={rule.title}
              >

                <div className="routingIcon">

                  {rule.type === "warning" && (
                    <WarningAmberOutlinedIcon />
                  )}

                  {rule.type === "security" && (
                    <SecurityOutlinedIcon />
                  )}

                  {rule.type === "claims" && (
                    <AssignmentOutlinedIcon />
                  )}

                </div>

                <div className="routingInfo">

                  <strong>{rule.title}</strong>

                  <span>
                    Route to: <b>{rule.route}</b>
                  </span>

                </div>

                <button className="ruleMenu">
                  ⋮
                </button>

              </div>

            ))}

            <button className="addRuleButton">
              <AddIcon />
              Add routing rule
            </button>

          </div>

          <div className="workflowActions">

            <button className="discardButton">
              Discard changes
            </button>

            <button className="saveButton">
              Save changes
            </button>

          </div>

        </div>
      )}

    </div>
  );
}

function PermissionIcon({
  allowed,
}: {
  allowed: boolean;
}) {
  return (
    <div
      className={
        allowed
          ? "permissionIcon allowed"
          : "permissionIcon denied"
      }
    >
      {allowed ? <CheckIcon /> : <CloseIcon />}
    </div>
  );
}