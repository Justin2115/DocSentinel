import { useEffect, useState } from "react";
import type { FormEvent } from "react";

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
import {
  addWorkflowRule,
  deleteWorkflowRule,
  getAdminUsers,
  getPermissionsMatrix,
  getWorkflowConfig,
  inviteAdminUser,
  permanentlyDeleteAdminUser,
  updateAdminUser,
  updatePermissionsMatrix,
  updateUserRole,
  updateWorkflowConfig,
} from "../api/auth";
import type { FolderPermissionItem, User, WorkflowConfig, WorkflowRule } from "../api/auth";
import { useAuth } from "../context/AuthContext";

type Tab = "users" | "permissions" | "workflow";
type RoleValue =
  | "SUPER_ADMIN"
  | "DEPARTMENT_ADMIN"
  | "DEPARTMENT_CHECKER"
  | "DEPARTMENT_MAKER";

const allRoles: { value: RoleValue; label: string }[] = [
  { value: "SUPER_ADMIN", label: "Super Admin" },
  { value: "DEPARTMENT_ADMIN", label: "Department Admin" },
  { value: "DEPARTMENT_CHECKER", label: "Department Checker" },
  { value: "DEPARTMENT_MAKER", label: "Department Maker" },
];

function normalizeRole(role: string): RoleValue {
  const clean = (role || "").toUpperCase().replace(/\s+/g, "_");
  if (clean === "SUPER_ADMIN" || clean === "ADMIN") return "SUPER_ADMIN";
  if (clean === "DEPARTMENT_ADMIN" || clean === "DEPT_ADMIN") return "DEPARTMENT_ADMIN";
  if (clean === "DEPARTMENT_CHECKER" || clean === "UPLOAD_CHECKER" || clean === "CHECKER") return "DEPARTMENT_CHECKER";
  return "DEPARTMENT_MAKER";
}

function roleLabel(role: string) {
  switch (normalizeRole(role)) {
    case "SUPER_ADMIN":
      return "Super Admin";
    case "DEPARTMENT_ADMIN":
      return "Department Admin";
    case "DEPARTMENT_CHECKER":
      return "Department Checker";
    case "DEPARTMENT_MAKER":
      return "Department Maker";
    default:
      return role;
  }
}

function getErrorMessage(error: unknown, fallback: string) {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

function initials(name: string) {
  return name
    .trim()
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase() || "U";
}

export default function Settings() {
  const { user: currentUser, isAdmin, isSuperAdmin, isDeptAdmin, refreshUser } = useAuth();
  const [inviteDept, setInviteDept] = useState(isDeptAdmin ? currentUser?.department || "HR" : "HR");
  const [activeTab, setActiveTab] = useState<Tab>("users");
  const [users, setUsers] = useState<User[]>([]);
  const [permissions, setPermissions] = useState<FolderPermissionItem[]>([]);
  const [workflow, setWorkflow] = useState<WorkflowConfig | null>(null);
  const [draftThreshold, setDraftThreshold] = useState(80);
  const [draftRules, setDraftRules] = useState<WorkflowRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteName, setInviteName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [invitePassword, setInvitePassword] = useState("");
  const [inviteRole, setInviteRole] = useState<RoleValue>("DEPARTMENT_MAKER");

  useEffect(() => {
    let mounted = true;
    Promise.all([getAdminUsers(), getPermissionsMatrix(), getWorkflowConfig()])
      .then(([userList, permissionMatrix, workflowConfig]) => {
        if (!mounted) return;
        setUsers(userList);
        setPermissions(permissionMatrix);
        setWorkflow(workflowConfig);
        setDraftThreshold(workflowConfig.threshold);
        setDraftRules(workflowConfig.rules);
      })
      .catch((loadError: unknown) => {
        if (mounted) setError(getErrorMessage(loadError, "Unable to load settings."));
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const clearFeedback = () => {
    setError(null);
    setSuccess(null);
  };

  const handleInvite = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    clearFeedback();

    const selectedDept = isDeptAdmin
      ? currentUser?.department || null
      : inviteRole === "SUPER_ADMIN"
      ? (inviteDept.trim() || null)
      : inviteDept.trim();

    if (inviteRole !== "SUPER_ADMIN" && !selectedDept) {
      setError("A valid department must be selected for Department Admin, Checker, and Maker roles.");
      return;
    }

    setWorking(true);
    try {
      const created = await inviteAdminUser({
        name: inviteName.trim(),
        email: inviteEmail.trim(),
        role: inviteRole,
        department: selectedDept,
        password: invitePassword.trim() || undefined,
      });
      setUsers((previous) => [...previous, created].sort((left, right) => left.id - right.id));
      setInviteName("");
      setInviteEmail("");
      setInvitePassword("");
      setInviteRole("DEPARTMENT_MAKER");
      setInviteDept(isDeptAdmin ? currentUser?.department || "HR" : "HR");
      setInviteOpen(false);
      if (invitePassword.trim()) {
        setSuccess(`Member ${created.name} successfully created with active login credentials.`);
      } else {
        setSuccess(`Member ${created.name} invited. They can sign in with their matching Google account.`);
      }
    } catch (inviteError: unknown) {
      setError(getErrorMessage(inviteError, "Unable to invite this member."));
    } finally {
      setWorking(false);
    }
  };

  const handleRoleChange = async (member: User, role: RoleValue) => {
    if (role !== "SUPER_ADMIN" && !member.department) {
      setError("Please assign a department to this user before or when setting a non-Super-Admin role.");
      return;
    }
    clearFeedback();
    setWorking(true);
    try {
      const updated = await updateUserRole(member.id, role);
      setUsers((previous) => previous.map((item) => (item.id === updated.id ? updated : item)));
      if (member.id === currentUser?.id) await refreshUser();
      setSuccess(`Role for ${member.name} updated to ${roleLabel(updated.role)}.`);
    } catch (updateError: unknown) {
      setError(getErrorMessage(updateError, "Unable to update this member's role."));
    } finally {
      setWorking(false);
    }
  };

  const handleDepartmentChange = async (member: User, dept: string) => {
    clearFeedback();
    setWorking(true);
    try {
      const updated = await updateAdminUser(member.id, { department: dept });
      setUsers((previous) => previous.map((item) => (item.id === updated.id ? { ...item, department: updated.department } : item)));
      if (member.id === currentUser?.id) await refreshUser();
      setSuccess(`Department for ${member.name} updated to ${updated.department || "Cross-Department"}.`);
    } catch (deptError: unknown) {
      setError(getErrorMessage(deptError, "Unable to update department."));
    } finally {
      setWorking(false);
    }
  };

  const handleToggleActive = async (member: User) => {
    clearFeedback();
    setWorking(true);
    try {
      const updated = await updateAdminUser(member.id, { is_active: !member.is_active });
      setUsers((previous) => previous.map((item) => (item.id === member.id ? { ...item, is_active: updated.is_active } : item)));
      setSuccess(`${member.name} has been ${updated.is_active ? "activated" : "deactivated"}.`);
    } catch (toggleError: unknown) {
      setError(getErrorMessage(toggleError, "Unable to update member status."));
    } finally {
      setWorking(false);
    }
  };

  const handlePermanentDelete = async (member: User) => {
    if (
      !window.confirm(
        `Are you sure you want to permanently delete ${member.name} (${member.email})?\n\n• The user will be completely removed from the user list.\n• All documents and historical audit records will be safely preserved.`
      )
    ) {
      return;
    }
    clearFeedback();
    setWorking(true);
    try {
      await permanentlyDeleteAdminUser(member.id);
      setUsers((previous) => previous.filter((item) => item.id !== member.id));
      setSuccess(`${member.name} has been permanently deleted.`);
    } catch (removeError: unknown) {
      setError(getErrorMessage(removeError, "Unable to permanently delete this member."));
    } finally {
      setWorking(false);
    }
  };

  const handlePermissionChange = (folder: string, role: "upload_maker" | "upload_checker", allowed: boolean) => {
    setPermissions((previous) => previous.map((item) => (item.folder === folder ? { ...item, [role]: allowed } : item)));
    clearFeedback();
  };

  const handleSavePermissions = async () => {
    clearFeedback();
    setWorking(true);
    try {
      const updated = await updatePermissionsMatrix(permissions);
      setPermissions(updated);
      setSuccess("Folder permissions saved.");
    } catch (permissionError: unknown) {
      setError(getErrorMessage(permissionError, "Unable to save folder permissions."));
    } finally {
      setWorking(false);
    }
  };

  const updateRule = (index: number, changes: Partial<WorkflowRule>) => {
    setDraftRules((previous) => previous.map((rule, currentIndex) => (currentIndex === index ? { ...rule, ...changes } : rule)));
    clearFeedback();
  };

  const handleAddRule = async () => {
    clearFeedback();
    setWorking(true);
    try {
      const created = await addWorkflowRule({
        title: "New routing rule",
        route: "Review team",
        type: "warning",
        threshold: draftThreshold,
        document_type: "",
      });
      setDraftRules((previous) => [...previous, created]);
      setWorkflow((previous) => (previous ? { ...previous, rules: [...previous.rules, created] } : previous));
      setSuccess("Routing rule added.");
    } catch (ruleError: unknown) {
      setError(getErrorMessage(ruleError, "Unable to add a routing rule."));
    } finally {
      setWorking(false);
    }
  };

  const handleDeleteRule = async (rule: WorkflowRule, index: number) => {
    if (!window.confirm(`Remove the routing rule "${rule.title}"?`)) return;
    clearFeedback();
    setWorking(true);
    try {
      if (rule.id !== undefined) await deleteWorkflowRule(rule.id);
      const remaining = draftRules.filter((_, currentIndex) => currentIndex !== index);
      setDraftRules(remaining);
      setWorkflow((previous) => (previous ? { ...previous, rules: remaining } : previous));
      setSuccess("Routing rule removed.");
    } catch (ruleError: unknown) {
      setError(getErrorMessage(ruleError, "Unable to remove this routing rule."));
    } finally {
      setWorking(false);
    }
  };

  const handleSaveWorkflow = async () => {
    clearFeedback();
    setWorking(true);
    try {
      const updated = await updateWorkflowConfig({
        threshold: draftThreshold,
        rules: draftRules.map((rule) => ({
          ...rule,
          document_type: rule.document_type?.trim() || null,
        })),
      });
      setWorkflow(updated);
      setDraftThreshold(updated.threshold);
      setDraftRules(updated.rules);
      setSuccess("Workflow settings saved.");
    } catch (workflowError: unknown) {
      setError(getErrorMessage(workflowError, "Unable to save workflow settings."));
    } finally {
      setWorking(false);
    }
  };

  const discardWorkflowChanges = () => {
    if (!workflow) return;
    setDraftThreshold(workflow.threshold);
    setDraftRules(workflow.rules);
    clearFeedback();
  };

  if (!isAdmin) {
    return <div className="settingsAccessDenied" role="alert">Access denied. Administrator privileges are required.</div>;
  }

  return (
    <div className="settingsPage">
      <div className="settingsHeader">
        <h1>Settings</h1>
        <p>Manage your team, permissions, and automation rules</p>
      </div>

      <div className="settingsTabs" role="tablist" aria-label="Administration settings">
        <button className={activeTab === "users" ? "settingsTab active" : "settingsTab"} onClick={() => setActiveTab("users")} role="tab" aria-selected={activeTab === "users"}>
          <GroupIcon /> Users &amp; roles
        </button>
        <button className={activeTab === "permissions" ? "settingsTab active" : "settingsTab"} onClick={() => setActiveTab("permissions")} role="tab" aria-selected={activeTab === "permissions"}>
          <ShieldOutlinedIcon /> Permissions
        </button>
        <button className={activeTab === "workflow" ? "settingsTab active" : "settingsTab"} onClick={() => setActiveTab("workflow")} role="tab" aria-selected={activeTab === "workflow"}>
          <AccountTreeOutlinedIcon /> Workflow rules
        </button>
      </div>

      {error && <div className="settingsNotice error" role="alert">{error}<button type="button" onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}
      {success && <div className="settingsNotice success" role="status">{success}<button type="button" onClick={() => setSuccess(null)} aria-label="Dismiss message">×</button></div>}
      {loading ? (
        <div className="settingsLoading" role="status">Loading settings…</div>
      ) : (
        <>
          {activeTab === "users" && (
            <section className="settingsSection" aria-label="Team members">
              <div className="sectionTop">
                <h2>Team members</h2>
                <button className="inviteButton" type="button" onClick={() => { clearFeedback(); setInviteOpen(true); }} disabled={working}>
                  <AddIcon /> Add / Invite member
                </button>
              </div>
              <div className="settingsTable">
                <div className="settingsTableHeader" style={{ display: "grid", gridTemplateColumns: "2.2fr 1.2fr 1.2fr 140px", alignItems: "center" }}>
                  <span>Member</span>
                  <span>Role</span>
                  <span>Department</span>
                  <span style={{ textAlign: "right" }}>Actions</span>
                </div>
                {users.length === 0 ? (
                  <div className="settingsEmpty">No members found.</div>
                ) : (
                  users.map((member) => {
                    const normRole = normalizeRole(member.role);
                    const isTargetSuperAdmin = normRole === "SUPER_ADMIN";
                    const isSelf = member.id === currentUser?.id;

                    return (
                      <div className="memberRow" key={member.id} style={{ display: "grid", gridTemplateColumns: "2.2fr 1.2fr 1.2fr 140px", alignItems: "center" }}>
                        <div className="memberInfo">
                          <MemberAvatar user={member} />
                          <div>
                            <strong>
                              {member.name}
                              {isSelf && <span style={{ marginLeft: 6, fontSize: "0.7rem", color: "var(--primary)" }}>(You)</span>}
                            </strong>
                            <span>{member.email}</span>
                            <div style={{ display: "flex", gap: "6px", alignItems: "center", marginTop: "2px" }}>
                              {!member.is_active ? (
                                <span className="memberStatus" style={{ background: "rgba(239, 68, 68, 0.15)", color: "#ef4444" }}>
                                  Deactivated
                                </span>
                              ) : (
                                <span style={{ fontSize: "0.7rem", color: "#10b981", fontWeight: 600 }}>Active</span>
                              )}
                              <span style={{ fontSize: "0.7rem", color: "var(--text-muted, #9ca3af)" }}>
                                {member.has_password ? "• Password set" : member.google_linked ? "• Google linked" : "• Setup pending"}
                              </span>
                            </div>
                          </div>
                        </div>

                        {/* Role selection */}
                        <select
                          className="roleSelect"
                          value={normRole}
                          disabled={
                            working ||
                            !member.is_active ||
                            isSelf ||
                            (!isSuperAdmin && isTargetSuperAdmin)
                          }
                          aria-label={`Role for ${member.name}`}
                          onChange={(event) => handleRoleChange(member, event.target.value as RoleValue)}
                        >
                          {(isSuperAdmin ? allRoles : allRoles.filter((r) => r.value !== "SUPER_ADMIN" && r.value !== "DEPARTMENT_ADMIN")).map((role) => (
                            <option key={role.value} value={role.value}>
                              {role.label}
                            </option>
                          ))}
                        </select>

                        {/* Department selector / badge */}
                        <div>
                          {isSuperAdmin && normRole !== "SUPER_ADMIN" ? (
                            <select
                              className="roleSelect"
                              value={member.department?.toUpperCase() || ""}
                              disabled={working || !member.is_active}
                              onChange={(e) => handleDepartmentChange(member, e.target.value)}
                              style={{
                                color: member.department ? "var(--text-primary)" : "#ef4444",
                                fontWeight: member.department ? 500 : 700,
                              }}
                            >
                              <option value="" disabled>⚠ Assign Dept</option>
                              <option value="HR">HR</option>
                              <option value="FINANCE">Finance</option>
                              <option value="LEGAL">Legal</option>
                              <option value="OPERATIONS">Operations</option>
                              <option value="IT">IT</option>
                            </select>
                          ) : (
                            <span
                              style={{
                                fontSize: "0.8rem",
                                fontWeight: 600,
                                color: member.department
                                  ? "var(--text-secondary)"
                                  : normRole === "SUPER_ADMIN"
                                  ? "var(--text-secondary)"
                                  : "#ef4444",
                              }}
                            >
                              {member.department || (normRole === "SUPER_ADMIN" ? "All Departments" : "⚠ Dept Unassigned")}
                            </span>
                          )}
                        </div>

                        {/* Actions: Toggle Deactivate/Activate and Permanent Delete */}
                        <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: "8px" }}>
                          <button
                            type="button"
                            disabled={
                              working ||
                              isSelf ||
                              (!isSuperAdmin && isTargetSuperAdmin)
                            }
                            onClick={() => handleToggleActive(member)}
                            style={{
                              background: "none",
                              border: "none",
                              cursor: isSelf ? "not-allowed" : "pointer",
                              fontSize: "0.75rem",
                              fontWeight: 600,
                              color: member.is_active ? "#ef4444" : "#10b981",
                              textDecoration: "underline",
                              padding: "4px",
                            }}
                            title={member.is_active ? "Deactivate member" : "Activate member"}
                          >
                            {member.is_active ? "Deactivate" : "Activate"}
                          </button>

                          <button
                            className="deleteMember"
                            type="button"
                            title={
                              isSelf
                                ? "You cannot delete your own account"
                                : isTargetSuperAdmin
                                ? "Cannot delete Super Admin account"
                                : "Permanently delete member"
                            }
                            aria-label={`Permanently delete ${member.name}`}
                            disabled={working || isSelf || isTargetSuperAdmin}
                            onClick={() => handlePermanentDelete(member)}
                            style={{
                              background: "none",
                              border: "none",
                              cursor: isSelf || isTargetSuperAdmin ? "not-allowed" : "pointer",
                              color: isSelf || isTargetSuperAdmin ? "var(--text-muted, #9ca3af)" : "#ef4444",
                              display: "flex",
                              alignItems: "center",
                              padding: "4px",
                            }}
                          >
                            <DeleteIcon fontSize="small" />
                          </button>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </section>
          )}

          {activeTab === "permissions" && (
            <section className="settingsSection" aria-label="Folder access permissions">
              <div className="sectionTop">
                <h2 className="sectionTitle">Folder access matrix</h2>
                <button type="button" className="saveButton" onClick={handleSavePermissions} disabled={working}>
                  <CheckIcon /> Save permissions
                </button>
              </div>
              {permissions.length === 0 ? (
                <div className="settingsEmpty">No folder permissions are configured.</div>
              ) : (
                <div className="permissionTable">
                  <div className="permissionHeader">
                    <span>Folder</span>
                    <span>Admin</span>
                    <span>Upload Maker</span>
                    <span>Upload Checker</span>
                  </div>
                  {permissions.map((item) => (
                    <div className="permissionRow" key={item.folder}>
                      <span className="folderName">{item.folder}</span>
                      <PermissionIcon allowed={item.admin} />
                      <label className="permissionToggle">
                        <input
                          type="checkbox"
                          checked={item.upload_maker}
                          disabled={working}
                          aria-label={`${item.upload_maker ? "Allow" : "Deny"} Upload Maker access to ${item.folder}`}
                          onChange={(event) => handlePermissionChange(item.folder, "upload_maker", event.target.checked)}
                        />
                      </label>
                      <label className="permissionToggle">
                        <input
                          type="checkbox"
                          checked={item.upload_checker}
                          disabled={working}
                          aria-label={`${item.upload_checker ? "Allow" : "Deny"} Upload Checker access to ${item.folder}`}
                          onChange={(event) => handlePermissionChange(item.folder, "upload_checker", event.target.checked)}
                        />
                      </label>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          {activeTab === "workflow" && (
            <section className="settingsSection" aria-label="Document workflow configuration">
              <h2 className="sectionTitle">Default confidence threshold</h2>
              <div className="thresholdCard">
                <div className="thresholdTop">
                  <div>
                    <h3>Auto-flag threshold</h3>
                    <p>Documents below this confidence are sent to the review queue</p>
                  </div>
                  <strong>{draftThreshold}%</strong>
                </div>
                <input
                  type="range"
                  min="50"
                  max="100"
                  value={draftThreshold}
                  disabled={working}
                  onChange={(event) => {
                    setDraftThreshold(Number(event.target.value));
                    clearFeedback();
                  }}
                  className="confidenceSlider"
                  aria-label="Auto-flag confidence threshold"
                />
                <div className="sliderLabels">
                  <span>50%</span>
                  <span>75%</span>
                  <span>100%</span>
                </div>
              </div>
              <h2 className="sectionTitle routingTitle">Routing rules</h2>
              <div className="routingRules">
                {draftRules.map((rule, index) => (
                  <div className="routingRule" key={rule.id ?? `new-${index}`}>
                    <div className="routingIcon">
                      {rule.type === "security" ? (
                        <SecurityOutlinedIcon />
                      ) : rule.type === "claims" ? (
                        <AssignmentOutlinedIcon />
                      ) : (
                        <WarningAmberOutlinedIcon />
                      )}
                    </div>
                    <div className="routingInfo">
                      <label>
                        Rule title
                        <input
                          value={rule.title}
                          disabled={working}
                          onChange={(event) => updateRule(index, { title: event.target.value })}
                        />
                      </label>
                      <label>
                        Document type
                        <input
                          value={rule.document_type ?? ""}
                          disabled={working}
                          onChange={(event) => updateRule(index, { document_type: event.target.value })}
                          placeholder="Any document type"
                        />
                      </label>
                      <label>
                        Route to
                        <input
                          value={rule.route}
                          disabled={working}
                          onChange={(event) => updateRule(index, { route: event.target.value })}
                        />
                      </label>
                      <div className="workflowRuleOptions">
                        <label>
                          Rule type
                          <select
                            value={rule.type}
                            disabled={working}
                            onChange={(event) => updateRule(index, { type: event.target.value })}
                          >
                            <option value="warning">Warning</option>
                            <option value="security">Security</option>
                            <option value="claims">Claims</option>
                          </select>
                        </label>
                        <label>
                          Threshold
                          <input
                            type="number"
                            min="50"
                            max="100"
                            value={rule.threshold ?? draftThreshold}
                            disabled={working}
                            onChange={(event) => updateRule(index, { threshold: Number(event.target.value) })}
                          />
                        </label>
                      </div>
                    </div>
                    <button
                      className="deleteMember"
                      type="button"
                      title="Remove routing rule"
                      aria-label={`Remove ${rule.title}`}
                      disabled={working}
                      onClick={() => handleDeleteRule(rule, index)}
                    >
                      <DeleteIcon />
                    </button>
                  </div>
                ))}
                <button className="addRuleButton" type="button" disabled={working} onClick={handleAddRule}>
                  <AddIcon /> Add routing rule
                </button>
              </div>
              <div className="workflowActions">
                <button className="discardButton" type="button" disabled={working || !workflow} onClick={discardWorkflowChanges}>
                  Discard changes
                </button>
                <button className="saveButton" type="button" disabled={working} onClick={handleSaveWorkflow}>
                  {working ? "Saving…" : "Save changes"}
                </button>
              </div>
            </section>
          )}
        </>
      )}

      {inviteOpen && (
        <div className="settingsModalOverlay" role="presentation">
          <form className="settingsModal" onSubmit={handleInvite} role="dialog" aria-modal="true" aria-labelledby="inviteTitle">
            <h2 id="inviteTitle">Add / Invite Team Member</h2>
            <p>Create a new account with password credentials or invite for Google OAuth sign-in.</p>
            <label>
              Full name
              <input autoFocus required maxLength={150} value={inviteName} onChange={(event) => setInviteName(event.target.value)} />
            </label>
            <label>
              Email address
              <input required type="email" maxLength={255} value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} />
            </label>
            <label>
              Password (for direct email/password login)
              <input
                type="password"
                minLength={6}
                value={invitePassword}
                onChange={(event) => setInvitePassword(event.target.value)}
                placeholder="Optional: leave blank for Google OAuth login only"
              />
            </label>
            <label>
              Role
              <select value={inviteRole} onChange={(event) => setInviteRole(event.target.value as RoleValue)}>
                {(isSuperAdmin ? allRoles : allRoles.filter((r) => r.value !== "SUPER_ADMIN" && r.value !== "DEPARTMENT_ADMIN")).map((role) => (
                  <option key={role.value} value={role.value}>
                    {role.label}
                  </option>
                ))}
              </select>
            </label>
            {isSuperAdmin ? (
              <label>
                Department
                {inviteRole === "SUPER_ADMIN" ? (
                  <select value={inviteDept} onChange={(event) => setInviteDept(event.target.value)}>
                    <option value="">Cross-Department (System-Wide)</option>
                    <option value="HR">HR</option>
                    <option value="FINANCE">Finance</option>
                    <option value="LEGAL">Legal</option>
                    <option value="OPERATIONS">Operations</option>
                    <option value="IT">IT</option>
                  </select>
                ) : (
                  <select required value={inviteDept} onChange={(event) => setInviteDept(event.target.value)}>
                    <option value="HR">HR</option>
                    <option value="FINANCE">Finance</option>
                    <option value="LEGAL">Legal</option>
                    <option value="OPERATIONS">Operations</option>
                    <option value="IT">IT</option>
                  </select>
                )}
              </label>
            ) : (
              <label>
                Department
                <input disabled value={currentUser?.department || "Assigned Department"} />
              </label>
            )}
            <div className="settingsModalActions">
              <button className="discardButton" type="button" disabled={working} onClick={() => setInviteOpen(false)}>
                Cancel
              </button>
              <button className="saveButton" type="submit" disabled={working}>
                {working ? "Creating…" : "Add Member"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

function PermissionIcon({ allowed }: { allowed: boolean }) {
  return (
    <div className={allowed ? "permissionIcon allowed" : "permissionIcon denied"} aria-label={allowed ? "Allowed" : "Denied"}>
      {allowed ? <CheckIcon /> : <CloseIcon />}
    </div>
  );
}

function MemberAvatar({ user }: { user: User }) {
  const [imageFailed, setImageFailed] = useState(false);
  if (user.profile_picture && !imageFailed) {
    return <img className="memberAvatar" src={user.profile_picture} alt="" onError={() => setImageFailed(true)} />;
  }
  return <div className="memberAvatar">{initials(user.name)}</div>;
}
